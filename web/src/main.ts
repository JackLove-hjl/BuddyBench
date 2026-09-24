import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import './styles/variables.css'
import './styles/base.css'
import './styles/markdown.css'
import './styles/code.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
