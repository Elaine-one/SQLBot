<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import { Chat, chatApi, ChatInfo, type ChatMessage, ChatRecord, questionApi } from '@/api/chat.ts'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import ChartBlock from '@/views/chat/chat-block/ChartBlock.vue'
import JSONBig from 'json-bigint'

const props = withDefaults(
  defineProps<{
    recordId?: number
    chatList?: Array<ChatInfo>
    currentChatId?: number
    currentChat?: ChatInfo
    message?: ChatMessage
    loading?: boolean
    reasoningName: 'sql_answer' | 'chart_answer' | Array<'sql_answer' | 'chart_answer'>
  }>(),
  {
    recordId: undefined,
    chatList: () => [],
    currentChatId: undefined,
    currentChat: () => new ChatInfo(),
    message: undefined,
    loading: false,
  }
)

const emits = defineEmits([
  'finish',
  'error',
  'stop',
  'scrollBottom',
  'update:loading',
  'update:chatList',
  'update:currentChat',
  'update:currentChatId',
])

const index = computed(() => {
  if (props.message?.index) {
    return props.message.index
  }
  if (props.message?.index === 0) {
    return 0
  }
  return -1
})

const _currentChatId = computed({
  get() {
    return props.currentChatId
  },
  set(v) {
    emits('update:currentChatId', v)
  },
})

const _currentChat = computed({
  get() {
    return props.currentChat
  },
  set(v) {
    emits('update:currentChat', v)
  },
})

const _chatList = computed({
  get() {
    return props.chatList
  },
  set(v) {
    emits('update:chatList', v)
  },
})

const _loading = computed({
  get() {
    return props.loading
  },
  set(v) {
    emits('update:loading', v)
  },
})

const stopFlag = ref(false)

// 流式状态：代替直接写 class 实例属性，确保 Vue 响应式追踪
const streamState = reactive<Record<string, any>>({})

const sendMessage = async () => {
  console.log('[ChartAnswer] sendMessage called, index:', index.value, 'question:', _currentChat.value.records[index.value]?.question)
  stopFlag.value = false
  _loading.value = true
  // 重置流式状态
  Object.keys(streamState).forEach(k => delete streamState[k])
  let hasNativeReasoning = false

  if (index.value < 0) {
    console.log('[ChartAnswer] sendMessage ABORT: index < 0')
    _loading.value = false
    return
  }

  const currentRecord: ChatRecord = _currentChat.value.records[index.value]

  let error: boolean = false
  if (_currentChatId.value === undefined) {
    error = true
  }
  if (error) return

  try {
    const controller: AbortController = new AbortController()
    const param = {
      question: currentRecord.question,
      chat_id: _currentChatId.value,
    }
    const response = await questionApi.add(param, controller)
    console.log('[ChartAnswer] got response, status:', response.status)
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    console.log('[ChartAnswer] starting SSE read loop')

    let sql_answer = ''
    let chart_answer = ''

    // Initialize tool_calls_log for this record if not already present
    if (!currentRecord.tool_calls_log) {
      currentRecord.tool_calls_log = []
    }

    let tempResult = ''

    while (true) {
      if (stopFlag.value) {
        controller.abort()
        break
      }

      const { done, value } = await reader.read()
      if (done) {
        console.log('[ChartAnswer] SSE stream ended (done=true)')
        _loading.value = false
        break
      }

      let chunk = decoder.decode(value, { stream: true })
      tempResult += chunk

      // Split on \n\n (SSE event boundary), not regex.
      // Regex with .* is greedy and breaks when event payload contains
      // nested JSON (e.g. chart config).  Splitting on \n\n is deterministic.
      const parts = tempResult.split('\n\n')
      // The last part may be incomplete — keep it for the next read
      tempResult = parts.pop() || ''

      for (const part of parts) {
        if (!part.startsWith('data:{')) continue

        try {
          const jsonStr = part.slice(5) // remove "data:" prefix
          const data = JSONBig.parse(jsonStr)

        if (data.code && data.code !== 200) {
            ElMessage({
              message: data.msg,
              type: 'error',
              showClose: true,
            })
            _loading.value = false
            return
          }

          switch (data.type) {
            case 'id':
              currentRecord.id = data.id
              _currentChat.value.records[index.value].id = data.id
              break
            case 'regenerate_record_id':
              currentRecord.regenerate_record_id = data.regenerate_record_id
              _currentChat.value.records[index.value].regenerate_record_id =
                data.regenerate_record_id
              break
            case 'question':
              currentRecord.question = data.question
              _currentChat.value.records[index.value].question = data.question
              break
            case 'info':
              console.info(data.msg)
              break
            case 'brief':
              _currentChat.value.brief = data.brief
              _chatList.value.forEach((c: Chat) => {
                if (c.id === _currentChat.value.id) {
                  c.brief = _currentChat.value.brief
                }
              })
              break
            case 'error':
              currentRecord.error = data.content
              emits('error', currentRecord.id)
              break
            case 'text-delta':
              sql_answer += (data.content || '')
              _currentChat.value.records[index.value].sql_answer = sql_answer
              break
            case 'tool-call':
              currentRecord.tool_calls_log.push({
                tool: data.tool_name,
                args: data.args,
                time: new Date(),
              })
              break
            case 'tool-result':
              // Tool call completed — update the last tool_call entry
              if (currentRecord.tool_calls_log.length > 0) {
                const last =
                  currentRecord.tool_calls_log[currentRecord.tool_calls_log.length - 1]
                last.result = data.summary || (data.success ? 'success' : 'failed')
              }
              break
            case 'sql-result':
              sql_answer += data.reasoning_content
              _currentChat.value.records[index.value].sql_answer = sql_answer
              break
            case 'sql':
              _currentChat.value.records[index.value].sql = data.content
              break
            case 'sql-data':
              console.log('[SSE] sql-data recordId:', _currentChat.value.records[index.value]?.id)
              getChatData(_currentChat.value.records[index.value].id)
              break
            case 'chart-result':
              chart_answer += data.reasoning_content
              _currentChat.value.records[index.value].chart_answer = chart_answer
              break
            case 'chart':
              console.log('[SSE] chart type:', (() => { try { return JSON.parse(data.content).type } catch(e) { return 'parse-error' } })())
              _currentChat.value.records[index.value].chart = data.content
              break
            case 'datasource':
              if (!_currentChat.value.datasource) {
                _currentChat.value.datasource = data.id
              }
              break
            case 'clarify':
              // Agent asks a clarification question — write to reactive streamState
              if (data.content) {
                streamState.clarify_question = data.content
              }
              break
            case 'reasoning':
              // DeepSeek native reasoning_content — write to reactive streamState
              if (data.content) {
                hasNativeReasoning = true
                streamState.sql_reasoning_content = (streamState.sql_reasoning_content || '') + data.content
              }
              break
            case 'execution-stats':
              // Agent execution log: token usage, tool timing, iterations
              try {
                _currentChat.value.records[index.value].execution_log = JSON.parse(data.content)
              } catch (e) { /* ignore parse errors */ }
              break
            case 'finish':
              console.log('[SSE] finish event, isTyping will be set to false')
              emits('finish', currentRecord.id)
              break
          }
          await nextTick()
        } catch (err) {
          console.error('SSE parse error:', err, 'raw:', part)
        }
      }
    }
  } catch (error) {
    if (!currentRecord.error) {
      currentRecord.error = ''
    }
    if (currentRecord.error.trim().length !== 0) {
      currentRecord.error = currentRecord.error + '\n'
    }
    currentRecord.error = currentRecord.error + 'Error:' + error
    console.error('Error:', error)
    emits('error')
  } finally {
    _loading.value = false
  }
}

const loadingData = ref(false)

function getChatData(recordId?: number) {
  console.log('[getChatData] fetching recordId:', recordId)
  if (!recordId) {
    console.error('[getChatData] recordId is null/undefined, skipping fetch')
    loadingData.value = false
    return
  }
  loadingData.value = true
  chatApi
    .get_chart_data(recordId)
    .then((response) => {
      console.log('[getChatData] success recordId:', recordId, 'hasData:', !!response?.data)
      _currentChat.value.records.forEach((record) => {
        if (record.id === recordId) {
          record.data = response
        }
      })
    })
    .catch((err) => {
      console.error('[getChatData] failed recordId:', recordId, 'error:', err)
    })
    .finally(() => {
      loadingData.value = false
      emits('scrollBottom')
    })
}

function stop() {
  stopFlag.value = true
  _loading.value = false
  emits('stop')
}

onBeforeUnmount(() => {
  stop()
})

onMounted(() => {
  if (props.message?.record?.id && props.message?.record?.finish) {
    getChatData(props.message.record.id)
  }
})

defineExpose({ sendMessage, index: () => index.value, stop })
</script>

<template>
  <BaseAnswer v-if="message" :message="message" :reasoning-name="reasoningName" :loading="_loading" :streaming-state="streamState">
    <ChartBlock
      style="margin-top: 6px"
      :message="message"
      :record-id="recordId"
      :loading-data="loadingData"
    />
    <slot></slot>
    <template #tool>
      <slot name="tool"></slot>
    </template>
    <template #footer>
      <slot name="footer"></slot>
    </template>
  </BaseAnswer>
</template>

<style scoped lang="less"></style>
