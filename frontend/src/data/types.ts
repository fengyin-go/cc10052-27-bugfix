/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
  entry?: EntryRow
}

export type DomainResult<T = Record<string, unknown>> = {
  ok: boolean
  message: string
  data: T
}

export type DimensionCatalog = {
  dimensions: { dimension: string; values: string[] }[]
}

export type SliceCredential = {
  id: number
  切片凭证编码: string
  实验编码: string
  切片维度: string
  切片值: string
  数据版次: number
  对照组数: number
  实验组数: number
  有效样本数: number
  剔除样本数: number
  分组差异: number
  样本明细: string
  凭证状态: string
  数据状态: string
  签发时间: string
}

export type SliceSet = {
  id: number
  切片集合编码: string
  实验编码: string
  数据版次: number
  切片凭证编码: string
  维度覆盖: string
  数据状态: string
  确认时间: string
}

export type MemberRow = {
  id: number
  用户标识: string
  分组名称: string
  终端: string
  分配批次: string
  分配时间: string
  status: string
  变更历史: string
}

export type DrilldownSlice = {
  切片凭证编码: string
  切片维度: string
  切片值: string
  数据版次: number
  对照组数: number
  实验组数: number
  有效样本数: number
  剔除样本数: number
  分组差异: number
  [field: string]: string | number
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
