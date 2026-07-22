<script setup lang="ts">
import { type ChatMessage } from '@/api/chat.ts'
import { computed, onMounted, ref, watch } from 'vue'
import MdComponent from '@/views/chat/component/MdComponent.vue'
import icon_up_outlined from '@/assets/svg/icon_up_outlined.svg'
import icon_down_outlined from '@/assets/svg/icon_down_outlined.svg'
import { useI18n } from 'vue-i18n'

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
  }>(),
  {
    loading: false,
  }
)

const { t } = useI18n()

const show = ref<boolean>(false)

// ❓ 是可靠边界（后端 clarify 事件保证），之前=思考，之后=回复
function splitByClarify(sql: string): { thinking: string; reply: string } {
  const idx = sql.indexOf('❓')
  if (idx >= 0) {
    return { thinking: sql.slice(0, idx).trim(), reply: sql.slice(idx).trim() }
  }
  return { thinking: sql, reply: '' }
}

const clarifyParts = computed(() => {
  const sql = props.message?.record?.sql_answer || ''
  return splitByClarify(sql)
})

// 思考区 = ❓ 之前的 LLM 输出 + chart_answer
const reasoningContent = computed<Array<string>>(() => {
  const result: Array<string> = []
  const thinkPart = clarifyParts.value.thinking
  if (thinkPart) result.push(thinkPart)
  const chart = props.message?.record?.chart_answer
  if (chart && chart.trim()) result.push(chart)
  return result
})

const hasThinking = computed<boolean>(() => reasoningContent.value.length > 0)

// 回复文本 = ❓ 之后的内容（AI 询问/回复用户的信息）
const replyText = computed<string>(() => clarifyParts.value.reply)

function clickShow() {
  show.value = !show.value
}

onMounted(() => {
  if (props.message.isTyping) {
    show.value = true
  }
})

// isTyping 变化时：开始→展开，结束→折叠
watch(() => props.message.isTyping, (typing) => {
  if (typing) {
    show.value = true
  } else if (hasThinking.value) {
    show.value = false
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

    <!-- 回复：AI 回复文本（❓后）+ 图表/表格 -->
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

  .answer-container {
    width: 100%;
    line-height: 24px;
    font-size: 16px;
    font-weight: 400;
    color: rgba(31, 35, 41, 1);
  }
}
</style>
