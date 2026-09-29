<script setup lang="ts">
import { onBeforeUnmount, onMounted, computed } from 'vue'
import { NConfigProvider, NMessageProvider, NDialogProvider, NSpin, darkTheme, zhCN, dateZhCN } from 'naive-ui'
import { useThemeStore } from './stores/theme'
import { useAuthStore } from './stores/auth'
import ImagePreview from './components/common/ImagePreview.vue'

const themeStore = useThemeStore()
const authStore = useAuthStore()

const theme = computed(() => (themeStore.resolved === 'dark' ? darkTheme : null))

onMounted(() => {
  themeStore.init()
  void authStore.restore()
})
onBeforeUnmount(() => {
  themeStore.dispose()
})
</script>

<template>
  <n-config-provider
    :theme="theme"
    :locale="zhCN"
    :date-locale="dateZhCN"
    :theme-overrides="{
      common: {
        primaryColor: '#4d6bfe',
        primaryColorHover: '#6c86ff',
        primaryColorPressed: '#3d5afe',
        borderRadius: '8px',
      },
    }"
  >
    <n-message-provider placement="top">
      <n-dialog-provider>
        <div class="app-shell">
          <n-spin v-if="authStore.initializing" class="app-loading" />
          <router-view v-else />
        </div>
        <!-- 图片放大浮层:全站只挂一次,任意组件用 useImagePreview().openImage 打开 -->
        <ImagePreview />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style>
/* 顶层 chain 上需要每个节点都是 flex column,否则 #app 的高度透不下去。
   用全局选择器强制 Naive UI 容器透传 height;浮层 (n-message/n-dialog) 不在此链上不受影响 */
.n-config-provider,
.n-message-provider,
.n-dialog-provider,
.app-shell {
  display: flex;
  flex-direction: column;
  flex: 1 1 0;
  min-height: 0;
}
.app-loading {
  flex: 1;
}
</style>
