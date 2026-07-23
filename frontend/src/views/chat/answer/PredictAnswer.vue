<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import { chatApi, ChatInfo, type ChatMessage, ChatRecord } from '@/api/chat.ts'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import MdComponent from '@/views/chat/component/MdComponent.vue'
import ChartBlock from '@/views/chat/chat-block/ChartBlock.vue'

const props = withDefaults(
  defineProps<{
    recordId?: number
    chatList?: Array<ChatInfo>
    currentChatId?: number
    currentChat?: ChatInfo
    message?: ChatMessage
    loading?: boolean
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
  'scrollBottom',
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

const streamState = reactive<Record<string, any>>({})

const sendMessage = async () => {
  stopFlag.value = false
  _loading.value = true
  Object.keys(streamState).forEach(k => delete streamState[k])
  let hasNativeReasoning = false

  if (index.value < 0) {
    _loading.value = false
    return
  }

  const currentRecord: ChatRecord = _currentChat.value.records[index.value]

  let error: boolean = false
  if (_currentChatId.value === undefined || currentRecord.predict_record_id === undefined) {
    error = true
  }
  if (error) return

  // Init tool_calls_log for thinking display
  if (!currentRecord.tool_calls_log) {
    currentRecord.tool_calls_log = []
  }

  try {
    const controller: AbortController = new AbortController()
    const response = await chatApi.predict(currentRecord.predict_record_id, controller)
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')

    let predict_answer = ''
    let predict_content = ''

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
                hasNativeReasoning = true
                predict_answer += data.content
                streamState.predict = predict_answer
                break
              case 'clarify':
                if (data.content) {
                  streamState.clarify_question = data.content
                }
                break
              case 'text-delta':
                predict_content += data.content
                _currentChat.value.records[index.value].predict_content = predict_content
                // Fallback：非推理模型用 text-delta 作为思考内容
                if (!hasNativeReasoning && data.content) {
                  streamState.predict = (streamState.predict || '') + data.content
                }
                break
              case 'tool-call':
                currentRecord.tool_calls_log.push({
                  tool: data.tool_name,
                  args: data.args,
                  time: new Date(),
                })
                break
              case 'chart':
                if (currentRecord.id) {
                  chatApi.get_chart_data(currentRecord.id).then((response) => {
                    if (response) {
                      currentRecord.data = response
                      console.log('[PredictAnswer] chart data loaded:', currentRecord.id)
                    }
                    currentRecord.chart = data.content
                  }).catch(() => {
                    currentRecord.chart = data.content
                  })
                } else {
                  currentRecord.chart = data.content
                }
                break
              case 'tool-result':
                if (currentRecord.tool_calls_log.length > 0) {
                  const last = currentRecord.tool_calls_log[currentRecord.tool_calls_log.length - 1]
                  last.result = data.summary || (data.success ? 'success' : 'failed')
                }
                break
              case 'execution-stats':
                try {
                  _currentChat.value.records[index.value].execution_log = JSON.parse(data.content)
                } catch (e) { /* ignore */ }
                break
              case 'finish':
                getChatPredictData(_currentChat.value.records[index.value].id)
                emits('finish', currentRecord.id)
                _loading.value = false
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

const chartBlockRef = ref()

const loadingData = ref(false)

function getChatPredictData(recordId?: number) {
  loadingData.value = true
  chatApi
    .get_chart_predict_data(recordId)
    .then((response) => {
      let has = false
      _currentChat.value.records.forEach((record) => {
        if (record.id === recordId) {
          has = true
          record.predict_data = response ?? []

          if (record.predict_data.length > 1) {
            getChatData(recordId)
          } else if (record.chart) {
            getChatData(recordId)  // Agent path: chart needs data for ChartBlock
          } else {
            loadingData.value = false
          }
        }
      })
      if (!has) {
        _loading.value = false
      }
    })
    .catch((e) => {
      loadingData.value = false
      console.error(e)
    })
}

function getChatData(recordId?: number) {
  loadingData.value = true
  chatApi
    .get_chart_data(recordId)
    .then((response) => {
      _currentChat.value.records.forEach((record) => {
        if (record.id === recordId) {
          record.data = response
        }
      })
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
    getChatPredictData(props.message.record.id)
  }
})

defineExpose({ sendMessage, index: () => index.value, chatList: () => _chatList, stop })
</script>

<template>
  <BaseAnswer v-if="message" :message="message" :reasoning-name="['predict']" :loading="_loading" :streaming-state="streamState">
    <MdComponent :message="message.record?.predict_content" style="margin-top: 12px" />
    <ChartBlock
      v-if="message.record?.predict_data?.length > 0 && message.record?.data"
      ref="chartBlockRef"
      style="margin-top: 12px"
      :record-id="recordId"
      :message="message"
      :loading-data="loadingData"
      is-predict
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
