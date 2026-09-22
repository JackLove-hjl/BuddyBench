<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../../utils/markdown'

const props = defineProps<{ content: string }>()

const html = computed(() => renderMarkdown(props.content || ''))
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- markdown-it 已 html:false 防 XSS -->
  <div class="md-body" v-html="html"></div>
</template>
