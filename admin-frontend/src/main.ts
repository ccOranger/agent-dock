import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
// 设计令牌与全局基础样式：必须放在 Element Plus 样式之后才能覆盖其默认值
import './styles/theme.css'
import App from './App.vue'
import router from './router'
import { initDensity } from './composables/useDensity'

// 表格密度在挂载前应用，避免首屏先按舒适密度渲染再跳变
initDensity()

// Element Plus 默认英文语言包，内置文案（表格空态、确认框按钮、日期面板等）会呈现英文。
// 以 zh-cn 为基线统一成中文，分页再按产品口径覆盖为「总数 N」/「N/页」。
const locale = {
  ...zhCn,
  name: 'zh-cn-cs',
  el: {
    ...zhCn.el,
    pagination: {
      ...zhCn.el.pagination,
      total: '总数 {total}',
      pagesize: '/页',
    },
  },
}

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale })
app.mount('#app')
