<template>
  <section class="page" data-module="report_gen">
    <header class="page-head">
      <div>
        <h2>报告生成器 · 下钻编排</h2>
        <p class="page-desc">
          每份报告只能采用一个已确认切片版本；维度更正后绑定旧集合的报告会生成失败，需改挂相容新版次集合。
        </p>
      </div>
      <div class="page-actions">
        <label>
          实验筛选
          <input v-model="expCode" data-testid="report-exp-filter" placeholder="EXP-SLICE-01" @change="reloadAll" />
        </label>
        <button class="btn" type="button" data-testid="report-reload" @click="reloadAll">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</p>

    <table class="data-table" data-testid="report-table">
      <thead>
        <tr>
          <th>报告编码</th><th>实验</th><th>状态</th><th>切片集合</th><th>切片版次</th>
          <th>失败原因</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="report in reports"
          :key="String(report.id)"
          :data-testid="`report-row-${report.id}`"
          :class="{ 'conflict-row': report.status === '生成失败' }"
        >
          <td>{{ report.报告编码 }}</td>
          <td>{{ report.实验编码 }}</td>
          <td>
            <span :class="report.status === '生成失败' ? 'tag tag-danger' : 'tag tag-live'">
              {{ report.status }}
            </span>
          </td>
          <td><code v-if="report.切片集合编码" class="credential-code">{{ report.切片集合编码 }}</code><span v-else>—</span></td>
          <td>{{ report.切片版次 ? `R${report.切片版次}` : '—' }}</td>
          <td>
            <span v-if="report.失败原因" class="insufficient-reason">⚠ {{ report.失败原因 }}</span>
            <span v-else>—</span>
          </td>
          <td class="row-actions">
            <button
              v-if="report.status === '排队中'"
              class="link"
              type="button"
              :data-testid="`generate-${report.id}`"
              @click="onGenerate(report.id)"
            >
              开始生成
            </button>
            <button
              v-if="report.status === '已完成'"
              class="link"
              type="button"
              :data-testid="`drilldown-${report.id}`"
              @click="onDrilldown(report.id)"
            >
              下钻章节
            </button>
            <button
              v-if="report.status === '已完成'"
              class="link"
              type="button"
              :data-testid="`replay-${report.id}`"
              @click="onReplay(report.id)"
            >
              按确认依据复算
            </button>
            <button
              v-if="report.status === '生成失败' || report.status === '排队中'"
              class="link"
              type="button"
              :data-testid="`rebind-${report.id}`"
              @click="onRebind(report)"
            >
              改挂现行集合
            </button>
          </td>
        </tr>
        <tr v-if="!reports.length"><td colspan="7" class="empty-state">暂无报告</td></tr>
      </tbody>
    </table>

    <section class="panel" style="margin-top: 16px">
      <h3>现行切片集合（可被新报告领用）</h3>
      <table class="data-table" data-testid="report-set-table">
        <thead><tr><th>集合编码</th><th>实验</th><th>版次</th><th>状态</th><th>覆盖</th></tr></thead>
        <tbody>
          <tr v-for="item in sliceSets" :key="item.切片集合编码">
            <td><code class="credential-code">{{ item.切片集合编码 }}</code></td>
            <td>{{ item.实验编码 }}</td>
            <td>R{{ item.数据版次 }}</td>
            <td>
              <span :class="item.数据状态 === '已过期' ? 'tag tag-stale' : 'tag tag-live'">
                {{ item.数据状态 }}
              </span>
            </td>
            <td>{{ describeCoverage(item.维度覆盖) }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 下钻章节：领取切片差异，并可回查样本明细与维度 -->
    <div v-if="drilldown" class="modal-backdrop" data-testid="drilldown-modal" @click.self="drilldown = null">
      <div class="modal modal-wide">
        <h3>下钻章节 · {{ drilldown.报告编码 }}（版次 R{{ drilldown.切片版次 }}）</h3>
        <div v-for="group in drilldown.下钻明细" :key="group.切片.切片凭证编码" class="drilldown-group">
          <h4>
            {{ group.切片.切片维度 }}={{ group.切片.切片值 }}
            <span class="muted-text">
              对照 {{ group.切片.对照组数 }} / 实验 {{ group.切片.实验组数 }} /
              分母 {{ group.切片.有效样本数 }} / 剔除 {{ group.切片.剔除样本数 }}
            </span>
            <span class="tag tag-live">差异 {{ group.切片.分组差异 }}</span>
          </h4>
          <table class="data-table">
            <thead><tr><th>用户标识</th><th>分组</th><th>{{ group.切片.切片维度 }}</th><th>分配批次</th></tr></thead>
            <tbody>
              <tr v-for="(sample, idx) in group.样本回查" :key="idx">
                <td>{{ sample.用户标识 }}</td>
                <td>{{ sample.分组名称 }}</td>
                <td>{{ sample[group.切片.切片维度] }}</td>
                <td>{{ sample.分配批次 }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="page-actions" style="margin-top: 12px">
          <button class="btn" type="button" @click="drilldown = null">关闭</button>
        </div>
      </div>
    </div>

    <!-- 旧报告复算：按冻结凭证快照复算，不采用后续更正 -->
    <div v-if="replay" class="modal-backdrop" data-testid="replay-modal" @click.self="replay = null">
      <div class="modal">
        <h3>旧报告复算 · {{ replay.报告编码 }}</h3>
        <p class="page-desc">
          集合 {{ replay.切片集合编码 }}（版次 R{{ replay.切片版次 }}），完成于 {{ replay.完成时间 }}；
          以下按报告冻结的凭证依据复算，不引用之后的维度更正。
        </p>
        <table class="data-table">
          <thead><tr><th>维度/取值</th><th>冻结差异</th><th>复算差异</th><th>复算分母</th></tr></thead>
          <tbody>
            <tr v-for="(item, idx) in replay.复算章节" :key="idx">
              <td>{{ item.切片维度 }}={{ item.切片值 }}</td>
              <td>{{ item.分组差异 }}</td>
              <td>{{ item.复算差异 }}</td>
              <td>{{ item.复算分母 }}</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions" style="margin-top: 12px">
          <button class="btn" type="button" @click="replay = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  fetchReportDrilldown,
  fetchSliceSets,
  listEntries,
  rebindSliceSet,
  replayReport,
  runAction,
} from '@/api/backend-service'
import type { EntryRow, SliceSet } from '@/data/types'

type DrilldownPayload = {
  报告编码: string
  切片版次: number
  下钻明细: {
    切片: Record<string, string | number>
    样本回查: Record<string, string>[]
  }[]
}

type ReplayPayload = {
  报告编码: string
  切片集合编码: string
  切片版次: number
  完成时间: string
  复算章节: Record<string, string | number>[]
}

const expCode = ref('EXP-SLICE-01')
const reports = ref<EntryRow[]>([])
const sliceSets = ref<SliceSet[]>([])
const errorMessage = ref('')
const noticeMessage = ref('')
const drilldown = ref<DrilldownPayload | null>(null)
const replay = ref<ReplayPayload | null>(null)

function describeCoverage(raw: string): string {
  try {
    const coverage = JSON.parse(raw) as { 切片维度: string; 切片值: string }[]
    return coverage.map((item) => `${item.切片维度}=${item.切片值}`).join('、')
  } catch {
    return raw || '—'
  }
}

async function reloadAll() {
  errorMessage.value = ''
  try {
    const [reportPage, setPage] = await Promise.all([
      listEntries('report_gen', expCode.value ? { 实验编码: expCode.value } : {}),
      fetchSliceSets(expCode.value),
    ])
    reports.value = reportPage.items
    sliceSets.value = setPage.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报告数据读取失败'
  }
}

async function onGenerate(id: number) {
  try {
    const result = await runAction('report_gen', id, '开始生成')
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报告生成失败'
    await reloadAll()
  }
}

async function onDrilldown(id: number) {
  try {
    const result = await fetchReportDrilldown(id)
    if (!result.ok) throw new Error(result.message)
    drilldown.value = result.data as unknown as DrilldownPayload
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '下钻数据读取失败'
  }
}

async function onReplay(id: number) {
  try {
    const result = await replayReport(id)
    if (!result.ok) throw new Error(result.message)
    replay.value = result.data as unknown as ReplayPayload
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报告复算失败'
  }
}

async function onRebind(report: EntryRow) {
  const current = sliceSets.value.find(
    (item) => item.实验编码 === report.实验编码 && item.数据状态 === '现行',
  )
  if (!current) {
    errorMessage.value = '该实验暂无现行切片集合，请先在切片分析器重新计算确认并生成集合'
    return
  }
  try {
    const result = await rebindSliceSet(Number(report.id), current.切片集合编码)
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '改挂切片集合失败'
  }
}

onMounted(reloadAll)
</script>
