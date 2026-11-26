import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  DimensionCatalog,
  DomainResult,
  EntryRow,
  MemberRow,
  OverviewResult,
  PageResult,
  SliceCredential,
  SliceSet,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---- 切片分析器领域接口 ----

async function domainPost<T = Record<string, unknown>>(
  path: string,
  body?: Record<string, unknown>,
): Promise<DomainResult<T>> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  })
  return await response.json()
}

async function domainGet<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error('领域数据读取失败')
  return (await response.json()) as T
}

export async function fetchDimensionCatalog(): Promise<DimensionCatalog> {
  return domainGet<DimensionCatalog>('/api/segment_slice/dimensions/catalog')
}

export async function registerSlice(payload: {
  切片批次: string
  实验编码: string
  切片维度: string
  切片值: string
}): Promise<DomainResult> {
  return domainPost('/api/segment_slice/slices', payload)
}

export async function runSlice(id: number): Promise<DomainResult> {
  return domainPost(`/api/segment_slice/${id}/run`)
}

export async function confirmSlice(id: number): Promise<DomainResult> {
  return domainPost(`/api/segment_slice/${id}/confirm`)
}

export async function buildSliceSet(expCode: string): Promise<DomainResult<{ slice_set: SliceSet }>> {
  return domainPost(`/api/segment_slice/experiments/${encodeURIComponent(expCode)}/slice-sets`)
}

export async function fetchSliceSets(expCode: string): Promise<{ ok: boolean; items: SliceSet[] }> {
  return domainGet(`/api/segment_slice/experiments/${encodeURIComponent(expCode)}/slice-sets`)
}

export async function fetchCredentials(expCode: string): Promise<{ ok: boolean; items: SliceCredential[] }> {
  return domainGet(`/api/segment_slice/experiments/${encodeURIComponent(expCode)}/credentials`)
}

export async function fetchCredentialSamples(
  credentialCode: string,
): Promise<DomainResult<{ 样本明细: Record<string, string>[] }>> {
  return domainGet(
    `/api/segment_slice/credentials/${encodeURIComponent(credentialCode)}/samples`,
  )
}

// ---- 分组分配器：维度更正 ----

export async function fetchExperimentMembers(expCode: string): Promise<{ ok: boolean; items: MemberRow[] }> {
  return domainGet(`/api/variant_assign/experiments/${encodeURIComponent(expCode)}/members`)
}

export async function correctDimension(
  id: number,
  dimension: string,
  newValue: string,
): Promise<DomainResult<{ 数据版次: number }>> {
  return domainPost(`/api/variant_assign/${id}/correct-dimension`, { 维度: dimension, 新取值: newValue })
}

// ---- 报告生成器：下钻、复算、改挂 ----

export async function replayReport(id: number): Promise<DomainResult> {
  return domainPost(`/api/report_gen/${id}/replay`)
}

export async function fetchReportDrilldown(id: number): Promise<DomainResult> {
  return domainGet(`/api/report_gen/${id}/drilldown`)
}

export async function rebindSliceSet(id: number, sliceSetCode: string): Promise<DomainResult> {
  return domainPost(`/api/report_gen/${id}/rebind-slice-set`, { 切片集合编码: sliceSetCode })
}

export async function queueDrilldownReport(expCode: string): Promise<DomainResult> {
  return domainPost(`/api/report_gen/experiments/${encodeURIComponent(expCode)}/queue-drilldown`)
}
