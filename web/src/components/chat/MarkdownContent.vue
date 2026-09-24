<script setup lang="ts">
import { computed } from 'vue'
import { useCodeCopy } from '../../composables/useCodeCopy'
import { renderMarkdown } from '../../utils/markdown'

const props = defineProps<{ content: string }>()

const html = computed(() => renderMarkdown(props.content || ''))
// 代码块的一键复制:按钮由 markdown 渲染进 HTML,这里用事件委托统一处理
const { onCodeCopyClick } = useCodeCopy()
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- markdown-it 已 html:false 防 XSS -->
  <div class="md-body" v-html="html" @click="onCodeCopyClick"></div>
</template>
