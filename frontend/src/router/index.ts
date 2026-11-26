import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import ReportDrilldown from '@/views/ReportDrilldown.vue'
import SliceWorkbench from '@/views/SliceWorkbench.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 切片分析器与报告生成器使用专用工作台，承载维度选样、凭证、版次与下钻编排
    { path: '/segment_slice', name: 'segment-slice', component: SliceWorkbench },
    { path: '/report_gen', name: 'report-drilldown', component: ReportDrilldown },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
