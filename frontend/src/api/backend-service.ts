import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

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

// ---- 资产归属交接与费用交接分录专用接口 ----

export type DomainBill = {
  归属域: string
  统计周期: string
  费用总额: number
  分录数: number
  冻结分录数: number
  来源分录: Record<string, string | boolean>[]
}

export type BillPayload = {
  bills: DomainBill[]
  scope_domains: string[]
  totals: {
    domain_totals: Record<string, number>
    fee_total: number
    entry_total: number
    balanced: boolean
    duplicate_entries: number
  }
}

export type TimelineRow = {
  id: number
  关联资产: string
  归属域: string
  负责人: string
  生效时间: string
  来源交接编号: string
  时间线条目状态: string
}

export async function fetchDomainBills(scope = ''): Promise<BillPayload> {
  const query = scope ? `?scope=${encodeURIComponent(scope)}` : ''
  const response = await request(`/api/cost_handoff/bills${query}`)
  if (!response.ok) throw new Error('域账单读取失败')
  return await response.json()
}

export async function fetchOwnershipTimeline(assetId = ''): Promise<{ items: TimelineRow[] }> {
  const query = assetId ? `?asset_id=${encodeURIComponent(assetId)}` : ''
  const response = await request(`/api/asset_ownership/timeline${query}`)
  if (!response.ok) throw new Error('归属时间线读取失败')
  return await response.json()
}

export async function changeAssetOwner(values: {
  asset_id: string
  new_domain: string
  new_owner: string
  handoff_at: string
}): Promise<ActionResult & { totals?: BillPayload['totals']; bills?: DomainBill[] }> {
  const response = await request('/api/asset_ownership/change-owner', {
    method: 'POST',
    body: JSON.stringify(values),
  })
  return await response.json()
}

export async function runFullSettlement(): Promise<{ ok: boolean; summary: Record<string, number> }> {
  const response = await request('/api/cost_handoff/settle', { method: 'POST' })
  if (!response.ok) throw new Error('重新结算失败')
  return await response.json()
}
