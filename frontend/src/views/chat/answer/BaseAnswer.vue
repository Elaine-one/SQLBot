<script setup lang="ts">
import { type ChatMessage } from '@/api/chat.ts'
import { computed, onMounted, ref, watch } from 'vue'
import type { Reactive } from 'vue'
import MdComponent from '@/views/chat/component/MdComponent.vue'
import icon_up_outlined from '@/assets/svg/icon_up_outlined.svg'
import icon_down_outlined from '@/assets/svg/icon_down_outlined.svg'
import { useI18n } from 'vue-i18n'
import { useChatConfigStore } from '@/stores/chatConfig.ts'

const props = withDefaults(
  defineProps<{
    message: ChatMessage
    loading?: boolean
    reasoningName:
      | 'sql_answer'
      | 'chart_answer'
      | 'analysis_thinking'
      | 'predict'
      | Array<'sql_answer' | 'chart_answer' | 'analysis_thinking' | 'predict'>
    /** 流式写入的思考/追问字段。Vue class 实例响应式不可靠，改用独立 reactive 对象。 */
    streamingState?: Record<string, any>
  }>(),
  {
    loading: false,
  }
)

const { t } = useI18n()
const chatConfig = useChatConfigStore()

// expand_thinking_block controls default state:
//   true  → always expanded (server-driven default)
//   false → collapsed unless typing, user can toggle
const expandThinking = chatConfig.getExpandThinkingBlock
const show = ref<boolean>(expandThinking)

const rn = computed(() => {
  const raw = props.reasoningName
  return Array.isArray(raw) ? raw : [raw]
})

/** 优先读 streamingState（流式），fallback 到 record 字段（DB 恢复） */
function _read(field: string): string {
  const ss = props.streamingState as Record<string, any> | undefined
  if (ss && ss[field]) return String(ss[field])
  return (props.message?.record as any)?.[field] || ''
}

const reasoningContent = computed<Array<string>>(() => {
  const result: Array<string> = []
  const rec = props.message?.record as any

  if (rn.value.includes('sql_answer')) {
    const r = _read('sql_reasoning_content')
    if (r.trim()) result.push(r)
  }
  if (rn.value.includes('analysis_thinking')) {
    const a = _read('analysis_thinking')
    if (a.trim()) result.push(a)
  }
  if (rn.value.includes('predict')) {
    const p = _read('predict')
    if (p.trim()) result.push(p)
  }
  return result
})

const hasThinking = computed<boolean>(() => reasoningContent.value.length > 0)

const clarifyQuestion = computed<string>(() => {
  return _read('clarify_question')
})

const replyText = computed<string>(() => {
  return props.message?.record?.sql_answer || ''
})

function clickShow() {
  show.value = !show.value
}

onMounted(() => {
  if (props.message.isTyping) {
    show.value = true
  } else if (!expandThinking && !hasThinking.value) {
    show.value = false
  }
})

watch(() => props.message.isTyping, (typing) => {
  if (typing) {
    show.value = true
  } else if (hasThinking.value) {
    // expand_thinking_block=true → keep open after streaming
    // expand_thinking_block=false → auto-collapse (user can re-open)
    show.value = expandThinking
  }
})
</script>

<template>
  <div class="base-answer-block">
    <!-- 思考过程：生成中默认展开，完成后可折叠 -->
    <el-button
      v-if="message.isTyping || hasThinking"
      class="thinking-btn"
      @click="clickShow"
    >
      <div class="thinking-btn-inner">
        <span v-if="message.isTyping">{{ t('qa.thinking') }}</span>
        <span v-else>{{ t('qa.thinking_step') }}</span>
        <span class="btn-icon">
          <el-icon v-if="show"><icon_up_outlined /></el-icon>
          <el-icon v-else><icon_down_outlined /></el-icon>
        </span>
      </div>
    </el-button>
    <div v-if="show && hasThinking" class="reasoning-content">
      <div v-for="(reason, _index) in reasoningContent" :key="'rc-' + _index" class="reasoning">
        <MdComponent :message="reason" />
      </div>
    </div>

    <!-- 追问卡片：Agent 向用户确认需求 -->
    <div v-if="clarifyQuestion" class="clarify-card">
      <div class="clarify-card-icon">💬</div>
      <div class="clarify-card-text">{{ clarifyQuestion }}</div>
    </div>

    <!-- 回复 -->
    <div class="answer-container">
      <div v-if="replyText" class="answer-text">
        <MdComponent :message="replyText" />
      </div>
      <slot></slot>
      <el-button v-if="message.isTyping" style="min-width: unset" type="primary" link loading />
      <slot name="tool"></slot>
      <slot name="footer"></slot>
    </div>
  </div>
</template>

<style scoped lang="less">
.base-answer-block {
  .thinking-btn {
    height: 32px;
    padding: 5px 12px;

    --ed-button-text-color: rgba(31, 35, 41, 1);
    --ed-button-hover-text-color: var(--ed-button-text-color);
    --ed-button-active-text-color: var(--ed-button-text-color);
    --ed-button-bg-color: rgba(255, 255, 255, 1);
    --ed-button-hover-bg-color: rgba(245, 246, 247, 1);
    --ed-button-active-bg-color: rgba(239, 240, 241, 1);
    --ed-button-border-color: rgba(217, 220, 223, 1);
    --ed-button-hover-border-color: var(--ed-button-border-color);
    --ed-button-active-border-color: var(--ed-button-border-color);

    --ed-button-font-weight: 400;

    .thinking-btn-inner {
      display: flex;
      flex-direction: row;
      align-items: center;
      line-height: 22px;
      font-weight: 400;
      font-size: 14px;
    }
    .btn-icon {
      margin-left: 4px;
    }
  }

  .reasoning-content {
    margin-top: 8px;
    display: flex;
    flex-direction: column;
    padding-left: 9px;
    border-left: 1px solid rgba(31, 35, 41, 0.15);
    gap: 8px;

    .reasoning {
      width: 100%;
      line-height: 22px;
      font-weight: 400;
      font-size: 14px;
      color: rgba(143, 149, 158, 1) !important;

      .markdown-body {
        color: rgba(143, 149, 158, 1) !important;
        line-height: 22px;
        font-weight: 400;
        font-size: 14px;
      }

      padding-bottom: 8px;
      border-bottom: 1px solid rgba(31, 35, 41, 0.15);

      &:last-child {
        padding-bottom: unset;
        border-bottom: unset;
      }
    }
  }

  .clarify-card {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin-top: 12px;
    padding: 12px 16px;
    border-radius: 8px;
    background: rgba(102, 126, 234, 0.06);
    border-left: 3px solid rgba(102, 126, 234, 0.5);

    .clarify-card-icon {
      font-size: 18px;
      line-height: 24px;
      flex-shrink: 0;
    }

    .clarify-card-text {
      font-size: 14px;
      line-height: 22px;
      color: rgba(31, 35, 41, 0.85);
      white-space: pre-wrap;
    }
  }

  .answer-container {
    width: 100%;
    line-height: 24px;
    font-size: 16px;
    font-weight: 400;
    color: rgba(31, 35, 41, 1);
  }
}
</style>
