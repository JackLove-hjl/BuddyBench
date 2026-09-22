import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('../pages/LoginPage.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      name: 'new',
      component: () => import('../pages/ChatPage.vue'),
    },
    {
      path: '/chat/:id',
      name: 'chat',
      component: () => import('../pages/ChatPage.vue'),
      props: true,
    },
  ],
})

// 全局守卫:未登录(无 token)只能访问公开页
router.beforeEach((to) => {
  const token = localStorage.getItem('llm-token')
  if (!to.meta.public && !token) {
    return { path: '/login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : {} }
  }
  if (to.path === '/login' && token) {
    return { path: '/' }
  }
  return true
})
