<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ChatLogHistoryItem } from '@/api/chat.ts'

const props = withDefaults(
  defineProps<{
    item?: ChatLogHistoryItem
  }>(),
  { item: undefined }
)

const showing = ref<'args' | 'result' | null>(null)

const toolData = computed(() => (props.item?.item as Record<string, any>) ?? {})
const summary = computed(() => toolData.value.summary || '')

const argsJson = computed(() => {
  try {
    return JSON.stringify(toolData.value.args || {}, null, 2)
  } catch {
    return '{}'
  }
})
const resultJson = computed(() => {
  try {
    return JSON.stringify(toolData.value.result || {}, null, 2)
  } catch {
    return '{}'
  }
})

function toggle(section: 'args' | 'result') {
  showing.value = showing.value === section ? null : section
}
</script>

<template>
  <div class="agent-tool-detail">
    <div class="summary-row">{{ summary }}</div>
    <div class="tabs">
      <button
        :class="{ active: showing === 'args' }"
        @click="toggle('args')"
      >
        入参
      </button>
      <button
        :class="{ active: showing === 'result' }"
        @click="toggle('result')"
      >
        返回
      </button>
    </div>
    <pre v-if="showing === 'args'" class="json-block">{{ argsJson }}</pre>
    <pre v-if="showing === 'result'" class="json-block">{{ resultJson }}</pre>
  </div>
</template>

<style scoped>
.agent-tool-detail {
  padding: 4px 0;
}
.summary-row {
  font-size: 13px;
  color: #646a73;
  margin-bottom: 8px;
}
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.tabs button {
  border: 1px solid #dee0e3;
  border-radius: 6px;
  padding: 4px 12px;
  font-size: 12px;
  background: #f5f6f7;
  color: #646a73;
  cursor: pointer;
}
.tabs button.active {
  background: #3370ff;
  color: #fff;
  border-color: #3370ff;
}
.json-block {
  background: #f5f6f7;
  border-radius: 8px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.6;
  color: #1f2329;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
