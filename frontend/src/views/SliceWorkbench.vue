<template>
  <section class="page" data-module="segment_slice">
    <header class="page-head">
      <div>
        <h2>切片分析器</h2>
        <p class="page-desc">
          选择实验维度与取值后计算分组差异，确认后签发报告切片凭证；缺组切片会明确标记，不能被当作完整证据。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="reload-slice" @click="reloadAll">刷新数据</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="slice-error">{{ errorMessage }}</p>
    <p v-if="noticeMessage" class="notice-text" data-testid="slice-notice">{{ noticeMessage }}</p>

    <!-- 计算前：所选维度、取值与当前数据版次在同一位置确认 -->
    <form class="analysis-form" data-testid="slice-option-form" @submit.prevent="registerAndRun">
      <label>
        实验编码
        <input v-model="option.expCode" data-testid="option-exp" placeholder="如 EXP-SLICE-01" required />
      </label>
      <label>
        切片维度
        <select v-model="option.dimension" data-testid="option-dimension" required>
          <option v-for="dim in dimensions" :key="dim.dimension" :value="dim.dimension">
            {{ dim.dimension }}
          </option>
        </select>
      </label>
      <label>
        切片取值
        <select v-model="option.value" data-testid="option-value" required>
          <option v-for="value in currentValues" :key="value" :value="value">{{ value }}</option>
        </select>
      </label>
      <button class="btn primary" data-testid="submit-slice-option" type="submit" :disabled="busy">
        {{ busy ? '计算中…' : '提交选项并开始计算' }}
      </button>
    </form>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">当前实验数据版次</span>
        <strong class="stat-value" data-testid="current-revision">{{ currentRevision ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">现行确认切片</span>
        <strong class="stat-value">{{ currentConfirmedCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">证据不足切片</span>
        <strong class="stat-value" data-testid="insufficient-count">{{ insufficientCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已冻结旧版次</span>
        <strong class="stat-value" data-testid="stale-count">{{ staleCount }}</strong>
      </article>
    </div>

    <!-- 切片结果列表：计算后在同一行确认维度、取值与实际有效组别 -->
    <h3>切片结果列表</h3>
    <table class="data-table" data-testid="slice-table">
      <thead>
        <tr>
          <th>切片批次</th>
          <th>维度</th>
          <th>取值</th>
          <th>对照组</th>
          <th>实验组</th>
          <th>有效样本(分母)</th>
          <th>已剔除</th>
          <th>分组差异</th>
          <th>版次</th>
          <th>数据状态</th>
          <th>当前状态</th>
          <th>凭证 / 不足依据</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :data-testid="`slice-row-${row.id}`"
          :data-status="String(row.status)"
          :class="{ 'stale-row': row.数据状态 === '已过期', 'insufficient-row': row.abnormal }"
        >
          <td>{{ row.切片批次 }}</td>
          <td>{{ row.切片维度 }}</td>
          <td>{{ row.切片值 }}</td>
          <td>{{ row.对照组数 ?? '—' }}</td>
          <td>{{ row.实验组数 ?? '—' }}</td>
          <td>{{ row.有效样本数 ?? '—' }}</td>
          <td>{{ row.剔除样本数 ?? '—' }}</td>
          <td>
            <span v-if="row.分组差异">{{ row.分组差异 }}</span>
            <span v-else class="muted-text">不输出</span>
          </td>
          <td>R{{ row.数据版次 ?? '—' }}</td>
          <td>
            <span :class="row.数据状态 === '已过期' ? 'tag tag-stale' : 'tag tag-live'">
              {{ row.数据状态 || '—' }}
            </span>
          </td>
          <td>
            <span :class="statusTagClass(row)">{{ row.status }}</span>
          </td>
          <td>
            <div v-if="row.不足依据" class="insufficient-reason" data-testid="insufficient-reason">
              ⚠ {{ row.不足依据 }}
            </div>
            <code v-else-if="row.切片凭证编码" class="credential-code">{{ row.切片凭证编码 }}</code>
            <span v-else class="muted-text">—</span>
          </td>
          <td class="row-actions">
            <button
              v-if="row.status === '待计算'"
              class="link"
              type="button"
              :data-testid="`run-${row.id}`"
              @click="onRun(row.id)"
            >
              开始计算
            </button>
            <button
              v-if="row.status === '计算失败'"
              class="link"
              type="button"
              :data-testid="`retry-${row.id}`"
              @click="onRun(row.id)"
            >
              重试计算
            </button>
            <button
              v-if="row.status === '已计算' && row.数据状态 === '现行'"
              class="link"
              type="button"
              :data-testid="`confirm-${row.id}`"
              @click="onConfirm(row.id)"
            >
              确认版本
            </button>
            <button
              v-if="row.切片凭证编码"
              class="link"
              type="button"
              :data-testid="`samples-${row.id}`"
              @click="onViewSamples(String(row.切片凭证编码))"
            >
              样本回查
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="13" class="empty-state">暂无切片，请在上方选择维度与取值后提交计算</td>
        </tr>
      </tbody>
    </table>

    <!-- 凭证与集合：报告编排只能领用现行确认集合 -->
    <div class="credential-grid">
      <section class="panel">
        <h3>报告切片凭证</h3>
        <table class="data-table" data-testid="credential-table">
          <thead>
            <tr><th>凭证编码</th><th>维度/取值</th><th>版次</th><th>差异</th><th>状态</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="cred in credentials" :key="cred.切片凭证编码">
              <td><code class="credential-code">{{ cred.切片凭证编码 }}</code></td>
              <td>{{ cred.切片维度 }}={{ cred.切片值 }}</td>
              <td>R{{ cred.数据版次 }}</td>
              <td>{{ cred.分组差异 }}</td>
              <td>
                <span :class="cred.数据状态 === '已过期' ? 'tag tag-stale' : 'tag tag-live'">
                  {{ cred.数据状态 }}
                </span>
              </td>
              <td>
                <button class="link" type="button" @click="onViewSamples(cred.切片凭证编码)">样本明细</button>
              </td>
            </tr>
            <tr v-if="!credentials.length"><td colspan="6" class="empty-state">暂无凭证</td></tr>
          </tbody>
        </table>
      </section>

      <section class="panel">
        <h3>切片集合（同次报告只能采用一个已确认版本）</h3>
        <div class="page-actions" style="margin-bottom: 8px">
          <button class="btn primary" type="button" data-testid="build-set" :disabled="busy" @click="onBuildSet">
            生成相容切片集合
          </button>
        </div>
        <table class="data-table" data-testid="slice-set-table">
          <thead>
            <tr><th>集合编码</th><th>版次</th><th>覆盖</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in sliceSets" :key="item.切片集合编码">
              <td><code class="credential-code">{{ item.切片集合编码 }}</code></td>
              <td>R{{ item.数据版次 }}</td>
              <td>{{ describeCoverage(item.维度覆盖) }}</td>
              <td>
                <span :class="item.数据状态 === '已过期' ? 'tag tag-stale' : 'tag tag-live'">
                  {{ item.数据状态 }}
                </span>
              </td>
            </tr>
            <tr v-if="!sliceSets.length"><td colspan="4" class="empty-state">暂无集合</td></tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- 维度更正：成员从一个维度值更正到另一个，版次推进、旧切片冻结 -->
    <section class="panel">
      <h3>成员维度更正（实验 {{ option.expCode || '—' }}）</h3>
      <form class="analysis-form" @submit.prevent="onCorrect">
        <label>
          成员记录ID
          <input v-model.number="correct.memberId" data-testid="correct-member-id" type="number" required />
        </label>
        <label>
          维度
          <input v-model="correct.dimension" data-testid="correct-dimension" readonly />
        </label>
        <label>
          更正后取值
          <select v-model="correct.newValue" data-testid="correct-new-value">
            <option v-for="value in currentValues" :key="value" :value="value">{{ value }}</option>
          </select>
        </label>
        <button class="btn primary" type="button" data-testid="apply-correct" :disabled="busy" @click="onCorrect">
          提交维度更正
        </button>
      </form>
      <table class="data-table" data-testid="member-table">
        <thead>
          <tr><th>ID</th><th>用户标识</th><th>分组</th><th>终端</th><th>状态</th><th>变更历史</th></tr>
        </thead>
        <tbody>
          <tr v-for="member in members" :key="member.id">
            <td>{{ member.id }}</td>
            <td>{{ member.用户标识 }}</td>
            <td>{{ member.分组名称 }}</td>
            <td>{{ member.终端 || '—' }}</td>
            <td>{{ member.status }}</td>
            <td>{{ member.变更历史 || '—' }}</td>
          </tr>
          <tr v-if="!members.length"><td colspan="6" class="empty-state">输入实验编码并刷新后查看成员</td></tr>
        </tbody>
      </table>
    </section>

    <!-- 凭证样本明细弹窗：下钻回查样本与对应维度 -->
    <div v-if="sampleModal" class="modal-backdrop" data-testid="sample-modal" @click.self="sampleModal = null">
      <div class="modal">
        <h3>凭证样本明细 · {{ sampleModal.code }}</h3>
        <p class="page-desc">
          有效样本 {{ sampleModal.samples.length }} 条，已剔除样本不进入分母；每条样本保留所属维度取值。
        </p>
        <table class="data-table">
          <thead><tr><th>用户标识</th><th>分组</th><th>{{ sampleModal.dimension }}</th><th>分配批次</th></tr></thead>
          <tbody>
            <tr v-for="(sample, idx) in sampleModal.samples" :key="idx">
              <td>{{ sample.用户标识 }}</td>
              <td>{{ sample.分组名称 }}</td>
              <td>{{ sample[sampleModal.dimension] }}</td>
              <td>{{ sample.分配批次 }}</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions" style="margin-top: 12px">
          <button class="btn" type="button" @click="sampleModal = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  buildSliceSet,
  confirmSlice,
  correctDimension,
  fetchCredentialSamples,
  fetchCredentials,
  fetchDimensionCatalog,
  fetchExperimentMembers,
  fetchSliceSets,
  listEntries,
  registerSlice,
  runSlice,
} from '@/api/backend-service'
import type { DimensionCatalog, EntryRow, MemberRow, SliceCredential, SliceSet } from '@/data/types'

const rows = ref<EntryRow[]>([])
const credentials = ref<SliceCredential[]>([])
const sliceSets = ref<SliceSet[]>([])
const members = ref<MemberRow[]>([])
const catalog = ref<DimensionCatalog>({ dimensions: [] })
const currentRevision = ref<number | null>(null)
const errorMessage = ref('')
const noticeMessage = ref('')
const busy = ref(false)

const option = ref({ expCode: 'EXP-SLICE-01', dimension: '终端', value: 'mobile' })
const correct = ref({ memberId: undefined as number | undefined, dimension: '终端', newValue: 'desktop' })
const sampleModal = ref<{ code: string; dimension: string; samples: Record<string, string>[] } | null>(null)

const dimensions = computed(() => catalog.value.dimensions)
const currentValues = computed(
  () => catalog.value.dimensions.find((item) => item.dimension === option.value.dimension)?.values ?? [],
)
const currentConfirmedCount = computed(
  () => rows.value.filter((row) => row.status === '已确认' && row.数据状态 === '现行').length,
)
const insufficientCount = computed(() => rows.value.filter((row) => row.不足依据).length)
const staleCount = computed(() => rows.value.filter((row) => row.数据状态 === '已过期').length)

function statusTagClass(row: EntryRow): string {
  if (row.abnormal) return 'tag tag-danger'
  if (row.status === '已确认') return 'tag tag-live'
  if (row.数据状态 === '已过期') return 'tag tag-stale'
  return 'tag tag-pending'
}

function describeCoverage(raw: string): string {
  try {
    const coverage = JSON.parse(raw) as { 切片维度: string; 切片值: string }[]
    return coverage.map((item) => `${item.切片维度}=${item.切片值}`).join('、')
  } catch {
    return raw || '—'
  }
}

function flashError(message: string) {
  errorMessage.value = message
  noticeMessage.value = ''
}

function flashNotice(message: string) {
  noticeMessage.value = message
  errorMessage.value = ''
}

async function reloadAll() {
  errorMessage.value = ''
  try {
    const [slicePage, credPage, setPage, dims] = await Promise.all([
      listEntries('segment_slice', { 实验编码: option.value.expCode }),
      fetchCredentials(option.value.expCode),
      fetchSliceSets(option.value.expCode),
      fetchDimensionCatalog(),
    ])
    rows.value = slicePage.items
    credentials.value = credPage.items
    sliceSets.value = setPage.items
    catalog.value = dims
    const memberPage = await fetchExperimentMembers(option.value.expCode)
    members.value = memberPage.items
    const revisions = rows.value
      .map((row) => Number(row.数据版次))
      .filter((value) => Number.isFinite(value))
    currentRevision.value = revisions.length ? Math.max(...revisions) : null
  } catch (error) {
    flashError(error instanceof Error ? error.message : '切片数据读取失败')
  }
}

async function registerAndRun() {
  flashError('')
  busy.value = true
  try {
    const batchCode = `SG-${option.value.expCode}-${option.value.value}-${Date.now()}`
    const registered = await registerSlice({
      切片批次: batchCode,
      实验编码: option.value.expCode,
      切片维度: option.value.dimension,
      切片值: option.value.value,
    })
    if (!registered.ok) throw new Error(registered.message)
    const target = registered.data as unknown as EntryRow
    const result = await runSlice(Number(target.id))
    if (!result.ok) {
      // 证据不足等失败也要刷新列表，让缺组切片明确展示，不能被误选为完整证据
      flashError(result.message)
    } else {
      flashNotice(result.message)
    }
    await reloadAll()
  } catch (error) {
    flashError(error instanceof Error ? error.message : '切片计算失败')
  } finally {
    busy.value = false
  }
}

async function onRun(id: number) {
  try {
    const result = await runSlice(id)
    if (!result.ok) {
      flashError(result.message)
    } else {
      flashNotice(result.message)
    }
    await reloadAll()
  } catch (error) {
    flashError(error instanceof Error ? error.message : '切片计算失败')
  }
}

async function onConfirm(id: number) {
  try {
    const result = await confirmSlice(id)
    if (!result.ok) throw new Error(result.message)
    flashNotice(result.message)
    await reloadAll()
  } catch (error) {
    flashError(error instanceof Error ? error.message : '版本确认失败')
  }
}

async function onBuildSet() {
  try {
    const result = await buildSliceSet(option.value.expCode)
    if (!result.ok) throw new Error(result.message)
    flashNotice(result.message)
    await reloadAll()
  } catch (error) {
    flashError(error instanceof Error ? error.message : '切片集合生成失败')
  }
}

async function onCorrect() {
  if (correct.value.memberId === undefined) {
    flashError('请填写成员记录ID')
    return
  }
  try {
    const result = await correctDimension(
      correct.value.memberId,
      correct.value.dimension,
      correct.value.newValue,
    )
    if (!result.ok) throw new Error(result.message)
    flashNotice(`${result.message}（新版次 R${result.data.数据版次}，旧切片已冻结，请重新计算确认）`)
    await reloadAll()
  } catch (error) {
    flashError(error instanceof Error ? error.message : '维度更正失败')
  }
}

async function onViewSamples(code: string) {
  try {
    const result = await fetchCredentialSamples(code)
    if (!result.ok) throw new Error(result.message)
    const credential = credentials.value.find((item) => item.切片凭证编码 === code)
    sampleModal.value = {
      code,
      dimension: credential?.切片维度 ?? option.value.dimension,
      samples: result.data.样本明细,
    }
  } catch (error) {
    flashError(error instanceof Error ? error.message : '样本回查失败')
  }
}

onMounted(reloadAll)
</script>
