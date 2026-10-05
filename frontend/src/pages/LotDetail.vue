<template>
  <div>
    <h1>批次详情 #{{ id }}</h1>
    <p v-if="err" class="err">{{ err }}</p>
    <div v-if="d" class="card">
      <p><b>{{ d.name }}</b> · {{ layerLabel }} · 剩 {{ d.qty_remain }}{{ d.unit }}</p>
      <p>入库到期日:{{ d.expiry || '—' }}</p>
      <p v-if="d.override_days != null">手钉可放:{{ d.override_days }} 天(自 {{ d.override_set_on }} 起算)</p>
      <p>有效到期日:{{ d.effective_expiry || '—' }}<span v-if="d.days_left != null"> · 剩 {{ d.days_left }} 天</span></p>
      <p>紧急结论:<b :class="'lv-' + (d.level || 'ok')">{{ levelText }}</b></p>
      <template v-if="d.status === 'on_shelf'">
        <input type="number" v-model.number="days" min="1" step="1" placeholder="手钉可放天数(正整数)" />
        <button @click="pin">钉住</button>
        <button v-if="d.override_days != null" class="ghost" @click="unpin">取消钉</button>
      </template>
      <p v-else class="muted">批次已不在架({{ d.status }}),不可钉。</p>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, inject } from 'vue'
import { api } from '../api'
const props = defineProps({ id: String })
const d = ref(null)
const days = ref(3)
const err = ref('')
const refreshAlerts = inject('refreshAlerts', () => {})
const labels = { upper: '上层', mid: '中层', lower: '下层' }
const layerLabel = computed(() => labels[d.value?.layer] || d.value?.layer || '')
const levelText = computed(() =>
  d.value?.level === 'expired' ? '已过期' : d.value?.level === 'soon' ? '临期' : '正常')
async function load() { d.value = await api('/lots/' + props.id) }
async function submit(body) {
  err.value = ''
  try {
    // 后端返回同一共享函数的紧急结论;再刷顶条,两处对同一批结论一致
    d.value = await api('/lots/' + props.id + '/override', { method: 'PUT', body: JSON.stringify(body) })
    await refreshAlerts()
  } catch (e) { err.value = e.message }
}
function pin() { submit({ override_days: days.value }) }
function unpin() { submit({ override_days: null }) }
onMounted(async () => { try { await load() } catch (e) { err.value = e.message } })
</script>
