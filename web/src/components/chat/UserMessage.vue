<script setup lang="ts">
import { computed } from 'vue'
import { resolveAssetUrl } from '../../utils/asset'

const props = defineProps<{ content: string; attachments?: { url: string; filename: string; kind: 'image' | 'file' }[] }>()

const images = computed(() => props.attachments?.filter((a) => a.kind === 'image') || [])
const files = computed(() => props.attachments?.filter((a) => a.kind === 'file') || [])
</script>

<template>
  <div class="user-msg-row">
    <div class="user-msg">
      <div v-if="images.length" class="user-att-images">
        <img
          v-for="(img, i) in images"
          :key="img.url + i"
          :src="resolveAssetUrl(img.url)"
          :alt="img.filename"
          loading="lazy"
        />
      </div>
      <div v-if="files.length" class="user-att-files">
        <!-- 有 URL 的附件(本地文件上传)显示为下载链接;@ 引用文件无 URL 显示纯文本文件名 -->
        <a
          v-for="(f, i) in files"
          :key="f.url + i"
          class="user-att-file"
          :href="f.url ? resolveAssetUrl(f.url) : undefined"
          :target="f.url ? '_blank' : undefined"
          rel="noopener"
        >
          <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor">
            <path d="M3 1.5a1 1 0 0 1 1-1h5l4 4v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-12zM9 1.5v3h3l-3-3z" />
          </svg>
          <span class="user-att-file-name">{{ f.filename }}</span>
        </a>
      </div>
      <span v-if="content" class="user-msg-text">{{ content }}</span>
    </div>
  </div>
</template>

<style scoped>
.user-msg-row {
  display: flex;
  justify-content: flex-end;
  padding: 4px 0;
}
.user-msg {
  max-width: 76%;
  background: var(--user-bubble);
  color: var(--user-bubble-text);
  border: 1px solid var(--border);
  padding: 10px 14px;
  border-radius: 12px 12px 4px 12px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.user-att-images {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 6px;
}
.user-att-images img {
  max-width: 100%;
  max-height: 240px;
  border-radius: 8px;
  display: block;
}
.user-att-files {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 6px;
}
.user-att-file {
  display: flex;
  align-items: center;
  gap: 6px;
  color: inherit;
  text-decoration: none;
  background: rgba(255, 255, 255, 0.15);
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 13px;
}
.user-att-file:hover {
  text-decoration: none;
  background: rgba(255, 255, 255, 0.25);
}
.user-att-file-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  max-width: 240px;
}
.user-msg-text {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  font-size: 15px;
}
</style>
