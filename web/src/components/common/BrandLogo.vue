<script setup lang="ts">
/**
 * BuddyBench 品牌标识(单色,取父级 currentColor)。
 *
 * 造型语义:上面的对话气泡 = Buddy(伙伴),下面的凳面 + 两条凳腿 = Bench(工作台);
 * 气泡尾巴落在凳面上,合起来即"伙伴在工作台上干活"。
 * 气泡里的三个点是 `fill-rule="evenodd"` 掏空的,不是叠色 —— 侧栏 22px 下依然清晰。
 */
withDefaults(defineProps<{ size?: number }>(), { size: 24 })
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 64 64"
    fill="currentColor"
    role="img"
    aria-label="BuddyBench"
  >
    <path
      fill-rule="evenodd"
      d="M23 5h18a11 11 0 0 1 11 11v12a11 11 0 0 1-11 11h-6l-3 7-3-7h-6a11 11 0 0 1-11-11V16a11 11 0 0 1 11-11z M24 19a3 3 0 1 0 0 6a3 3 0 0 0 0-6z M32 19a3 3 0 1 0 0 6a3 3 0 0 0 0-6z M40 19a3 3 0 1 0 0 6a3 3 0 0 0 0-6z"
    />
    <rect x="6" y="43" width="52" height="7" rx="3.5" />
    <rect x="14" y="50" width="7" height="9" rx="3.5" />
    <rect x="43" y="50" width="7" height="9" rx="3.5" />
  </svg>
</template>
