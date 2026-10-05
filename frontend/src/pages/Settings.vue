<template>
  <div>
    <h1>设置</h1>
    <label class="muted">临期预警天数 warn_days(变基后顶条与消费资格仍同一基准)</label>
    <div>
      <input type="number" v-model.number="warn" min="0" step="1" />
      <button @click="save">保存</button>
      <span v-if="msg" class="muted"> {{ msg }}</span>
    </div>
    <pre>{{ s }}</pre>
  </div>
</template>
<script setup>
import { ref, onMounted, inject } from 'vue'
import { api } from '../api'
const s = ref('')
const warn = ref(3)
const msg = ref('')
const refreshAlerts = inject('refreshAlerts', () => {})
async function load() {
  const j = await api('/settings')
  s.value = JSON.stringify(j, null, 2)
  warn.value = Number(j.warn_days)
}
async function save() {
  try {
    await api('/settings', { method: 'PUT', body: JSON.stringify({ warn_days: warn.value }) })
    msg.value = '已保存'
    await load()
    await refreshAlerts()
  } catch (e) { msg.value = e.message }
}
onMounted(load)
</script>
