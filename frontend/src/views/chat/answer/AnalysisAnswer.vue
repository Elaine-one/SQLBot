<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import { chatApi, ChatInfo, type ChatMessage, ChatRecord } from '@/api/chat.ts'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import MdComponent from '@/views/chat/component/MdComponent.vue'
import ChartBlock from '@/views/chat/chat-block/ChartBlock.vue'
const props = withDefaults(
  defineProps<{
    chatList?: Array<ChatInfo>
    currentChatId?: number
    currentChat?: ChatInfo
    message?: ChatMessage
    loading?: boolean
  }>(),
  {
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
const sendMessage = async () => {
  stopFlag.value = false
  _loading.value = true

  if (index.value < 0) {
    _loading.value = false
    return
  }

  const currentRecord: ChatRecord = _currentChat.value.records[index.value]

  let error: boolean = false
  if (_currentChatId.value === undefined || currentRecord.analysis_record_id === undefined) {
    error = true
  }
  if (error) return

  // Init tool_calls_log for thinking display
  if (!currentRecord.tool_calls_log) {
    currentRecord.tool_calls_log = []
  }

  try {
    const controller: AbortController = new AbortController()
    const response = await chatApi.analysis(currentRecord.analysis_record_id, controller)
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')

    let analysis_answer = ''
    let analysis_answer_thinking = ''

    let tempResult = ''

    while (true) {
      if (stopFlag.value) {
        controller.abort()
        _loading.value = false
        break
      }

      const { done, value } = await reader.read()
      if (done) {
        _loading.value = false
        break
      }

      let chunk = decoder.decode(value, { stream: true })
      tempResult += chunk
      const split = tempResult.match(/data:.*}\n\n/g)
      if (split) {
        chunk = split.join('')
        tempResult = tempResult.replace(chunk, '')
      } else {
        continue
      }
      if (chunk && chunk.startsWith('data:{')) {
        if (split) {
          for (const str of split) {
            let data
            try {
              data = JSON.parse(str.replace('data:{', '{'))
            } catch (err) {
              console.error('JSON string:', str)
              throw err
            }

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
              case 'info':
                console.info(data.msg)
                break
              case 'error':
                currentRecord.error = data.content
                emits('error', currentRecord.id)
                break
              case 'reasoning':
                analysis_answer_thinking += data.content
                _currentChat.value.records[index.value].analysis_thinking = analysis_answer_thinking
                break
              case 'text-delta':
                analysis_answer += data.content
                _currentChat.value.records[index.value].analysis = analysis_answer
                // Capture reasoning_content from text-delta if present (fallback)
                if (data.reasoning_content) {
                  analysis_answer_thinking += data.reasoning_content
                  _currentChat.value.records[index.value].analysis_thinking = analysis_answer_thinking
                }
                break
              case 'tool-call':
                currentRecord.tool_calls_log.push({
                  tool: data.tool_name,
                  args: data.args,
                  time: new Date(),
                })
                break
              case 'tool-result':
                if (currentRecord.tool_calls_log.length > 0) {
                  const last = currentRecord.tool_calls_log[currentRecord.tool_calls_log.length - 1]
                  last.result = data.summary || (data.success ? 'success' : 'failed')
                }
                break
              case 'chart':
                // Fetch data FIRST, then set chart — so DisplayChartBlock
                // sees data?.length > 0 when it mounts
                if (currentRecord.id) {
                  chatApi.get_chart_data(currentRecord.id).then((response) => {
                    if (response) {
                      currentRecord.data = response
                      console.log('[AnalysisAnswer] chart data loaded:', currentRecord.id)
                    }
                    // Set chart AFTER data, so DisplayChartBlock renders correctly
                    currentRecord.chart = data.content
                  }).catch(() => {
                    currentRecord.chart = data.content  // fallback: set chart even if data fails
                  })
                } else {
                  currentRecord.chart = data.content
                }
                break
              case 'execution-stats':
                try {
                  _currentChat.value.records[index.value].execution_log = JSON.parse(data.content)
                } catch (e) { /* ignore */ }
                break
              case 'finish':
                emits('finish', currentRecord.id)
                break
            }
            await nextTick()
          }
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
function stop() {
  stopFlag.value = true
  _loading.value = false
  emits('stop')
}

onBeforeUnmount(() => {
  stop()
})
onMounted(() => {
  const rid = props.message?.record?.id
  const hasChart = props.message?.record?.chart
  console.log('[AnalysisAnswer] onMounted recordId:', rid, 'finish:', props.message?.record?.finish, 'hasChart:', !!hasChart)
  if (rid && hasChart) {
    chatApi.get_chart_data(rid).then((response) => {
      console.log('[AnalysisAnswer] getChatData success recordId:', rid, 'hasData:', !!response)
      _currentChat.value.records.forEach((record) => {
        if (record.id === rid) {
          record.data = response
        }
      })
    }).catch((e) => {
      console.error('[AnalysisAnswer] getChatData failed:', rid, e)
    })
  }
})

defineExpose({ sendMessage, index: () => index.value, chatList: () => _chatList.value, stop })
</script>

<template>
  <BaseAnswer
    v-if="message"
    :message="message"
    :reasoning-name="['analysis_thinking']"
    :loading="_loading"
  >
    <MdComponent :message="message.record?.analysis" style="margin-top: 12px" />
    <ChartBlock
      v-if="message.record?.chart"
      style="margin-top: 12px"
      :record-id="message.record?.id"
      :message="message"
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
