<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 资产归属交接：归属时间线 + 更改当前负责人 -->
    <section v-if="meta.key === 'asset_ownership'" class="handoff-panel" data-testid="ownership-panel">
      <h3>资产归属时间线</h3>
      <table class="data-table">
        <thead>
          <tr><th>关联资产</th><th>归属域</th><th>负责人</th><th>生效时间</th><th>来源交接编号</th></tr>
        </thead>
        <tbody>
          <tr v-for="line in timelineRows" :key="line.id">
            <td>{{ line.关联资产 }}</td>
            <td>{{ line.归属域 }}</td>
            <td>{{ line.负责人 }}</td>
            <td>{{ line.生效时间 }}</td>
            <td>{{ line.来源交接编号 }}</td>
          </tr>
        </tbody>
      </table>

      <h3>更改当前负责人（登记并生效一笔归属交接）</h3>
      <form class="filter-bar" @submit.prevent="submitOwnerChange">
        <label class="filter-item">
          <span>资产编号</span>
          <input v-model="ownerForm.asset_id" data-testid="owner-asset" placeholder="如 ASSE-0004" required />
        </label>
        <label class="filter-item">
          <span>新归属域</span>
          <input v-model="ownerForm.new_domain" data-testid="owner-domain" placeholder="如 华南数据域" required />
        </label>
        <label class="filter-item">
          <span>新负责人</span>
          <input v-model="ownerForm.new_owner" data-testid="owner-name" placeholder="留空则与域同名" />
        </label>
        <label class="filter-item">
          <span>交接点时间</span>
          <input v-model="ownerForm.handoff_at" data-testid="owner-time" placeholder="2026-11-22T00:00:00" required />
        </label>
        <button class="btn primary" data-testid="owner-submit" type="submit">确认交接并重算费用</button>
      </form>
      <p v-if="handoffMessage" class="page-desc" data-testid="handoff-message">{{ handoffMessage }}</p>
    </section>

    <!-- 费用交接分录：域账单聚合 + 权限视角 + 总额核对 -->
    <section v-if="meta.key === 'cost_handoff'" class="handoff-panel" data-testid="billing-panel">
      <div class="page-head">
        <h3>各域账单（按费用交接分录聚合）</h3>
        <div class="page-actions">
          <label class="filter-item">
            <span>权限视角（域，逗号分隔，留空看全量）</span>
            <input v-model="billScope" data-testid="bill-scope" placeholder="如 华北数据域" @change="reloadBills" />
          </label>
          <button class="btn" type="button" @click="reloadBills">刷新账单</button>
          <button class="btn primary" data-testid="resettle" type="button" @click="resettle">重新结算</button>
        </div>
      </div>
      <p v-if="billMessage" class="page-desc" data-testid="bill-message">{{ billMessage }}</p>
      <table class="data-table">
        <thead>
          <tr><th>归属域</th><th>统计周期</th><th>费用总额</th><th>分录数</th><th>冻结分录数</th><th>来源费用</th></tr>
        </thead>
        <tbody>
          <tr v-for="bill in domainBills" :key="`${bill.归属域}-${bill.统计周期}`">
            <td>{{ bill.归属域 }}</td>
            <td>{{ bill.统计周期 }}</td>
            <td>{{ bill.费用总额.toFixed(2) }}</td>
            <td>{{ bill.分录数 }}</td>
            <td>{{ bill.冻结分录数 }}</td>
            <td>
              <span v-for="src in bill.来源分录" :key="String(src.费用编号)" class="bill-source">
                {{ src.费用编号 }}（{{ src.费用金额 }}）
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <dl v-if="billTotals" class="totals-line" data-testid="bill-totals">
        <dt>域间合计：</dt>
        <dd v-for="(amount, domain) in billTotals.domain_totals" :key="domain">{{ domain }} {{ amount.toFixed(2) }} ｜</dd>
        <dd>费用总额 {{ billTotals.fee_total.toFixed(2) }} ＝ 分录总额 {{ billTotals.entry_total.toFixed(2) }}（{{ billTotals.balanced ? '平衡' : '不平衡' }}，重复归属 {{ billTotals.duplicate_entries }} 笔）</dd>
      </dl>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  changeAssetOwner,
  createEntry,
  downloadEntries,
  fetchDomainBills,
  fetchOwnershipTimeline,
  listEntries,
  moduleMeta,
  runAction as applyAction,
  runFullSettlement,
} from '@/api/backend-service'
import type { DomainBill, TimelineRow } from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

// 归属交接面板状态
const timelineRows = ref<TimelineRow[]>([])
const ownerForm = ref({ asset_id: 'ASSE-0004', new_domain: '', new_owner: '', handoff_at: '' })
const handoffMessage = ref('')

// 费用交接分录面板状态
const domainBills = ref<DomainBill[]>([])
const billScope = ref('')
const billTotals = ref<Awaited<ReturnType<typeof fetchDomainBills>>['totals'] | null>(null)
const billMessage = ref('')

const stats = computed(() => {
  const base = [
    { label: meta.metrics[0], value: total.value },
    { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
    {
      label: meta.metrics[2],
      value: rows.value.filter((row) => String(row.status) === meta.statuses[meta.statuses.length - 1]).length,
    },
  ]
  if (meta.key === 'cost_handoff') {
    // 费用交接分录第三个指标按冻结标记统计，而不是末状态数量
    base[2] = {
      label: meta.metrics[2],
      value: rows.value.filter((row) => Boolean(row.冻结标记)).length,
    }
  }
  return base
})
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
    await reloadPanels()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

async function reloadTimeline() {
  try {
    const payload = await fetchOwnershipTimeline()
    timelineRows.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归属时间线读取失败'
  }
}

async function submitOwnerChange() {
  errorMessage.value = ''
  handoffMessage.value = ''
  try {
    const result = await changeAssetOwner({
      asset_id: ownerForm.value.asset_id.trim(),
      new_domain: ownerForm.value.new_domain.trim(),
      new_owner: ownerForm.value.new_owner.trim(),
      handoff_at: ownerForm.value.handoff_at.trim(),
    })
    if (!result.ok) throw new Error(result.message)
    handoffMessage.value = result.message
    await reload()
    await reloadTimeline()
    await reloadBills()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '负责人更改失败'
  }
}

async function reloadBills() {
  try {
    const payload = await fetchDomainBills(billScope.value.trim())
    domainBills.value = payload.bills
    billTotals.value = payload.totals
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '域账单读取失败'
  }
}

async function resettle() {
  errorMessage.value = ''
  billMessage.value = ''
  try {
    const result = await runFullSettlement()
    billMessage.value = `重新结算完成：结算 ${result.summary.settled} 笔，冻结 ${result.summary.frozen} 笔，待结算 ${result.summary.pending} 笔`
    await reload()
    await reloadBills()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重新结算失败'
  }
}

async function reloadPanels() {
  if (props.moduleKey === 'asset_ownership') {
    await reloadTimeline()
  } else if (props.moduleKey === 'cost_handoff') {
    await reloadBills()
  }
}

onMounted(async () => {
  await reload()
  await reloadPanels()
})
</script>

<style scoped>
.handoff-panel {
  margin-top: 20px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
}
.handoff-panel h3 {
  margin: 4px 0 10px;
  font-size: 15px;
}
.bill-source {
  display: inline-block;
  margin-right: 8px;
  color: var(--brand);
  font-size: 12px;
}
.totals-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 12px 0 0;
  font-size: 13px;
}
.totals-line dt {
  color: var(--muted);
}
.totals-line dd {
  margin: 0;
}
</style>
