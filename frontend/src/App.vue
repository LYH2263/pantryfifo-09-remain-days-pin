<template>
  <div>
    <div class="alert-bar" v-if="alerts.length">临期预警：{{ alerts.map(a => a.name + (a.level === 'expired' ? '(已过期)' : '(剩' + a.days_left + '天)')).join(' · ') }}</div>
    <div class="alert-bar" v-else>临期预警带：暂无紧急批次</div>
    <div class="wrap">
      <nav class="layer-tabs">
        <router-link to="/">全层</router-link>
        <router-link to="/layer/upper">上层</router-link>
        <router-link to="/layer/mid">中层</router-link>
        <router-link to="/layer/lower">下层</router-link>
        <router-link to="/inbound">入库</router-link>
        <router-link to="/consume">消费</router-link>
        <router-link to="/settings">设置</router-link>
      </nav>
      <router-view />
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted, provide, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from './api'
const alerts = ref([])
async function load() { try { alerts.value = await api('/alerts') } catch { alerts.value = [] } }
// 批次详情钉值/设置改 warn_days 后调用,顶条与详情对同一批得出同一紧急结论
provide('refreshAlerts', load)
const route = useRoute()
watch(() => route.fullPath, load)
onMounted(load)
</script>
