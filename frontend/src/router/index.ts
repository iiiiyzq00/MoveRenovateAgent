import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'chat',
      component: () => import('../views/Chat.vue'),
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/Login.vue'),
    },
    {
      path: '/plans',
      name: 'plans',
      component: () => import('../views/Plans.vue'),
    },
    {
      path: '/plans/:id',
      name: 'plan-detail',
      component: () => import('../views/PlanDetail.vue'),
    },
  ],
})

export default router
