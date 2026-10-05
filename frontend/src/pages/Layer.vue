<template>
  <div>
    <h1>{{ props.layer }} 层</h1>
    <router-link v-for="x in rows" :key="x.id" class="lot" :to="'/lots/' + x.id">
      {{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}<span v-if="x.override_days != null" class="pin">钉{{ x.override_days }}天</span>
    </router-link>
  </div>
</template>
<script setup>
import { ref, watch, onMounted } from 'vue'
import { api } from '../api'
const props = defineProps({ layer: String })
const rows = ref([])
async function load() { rows.value = await api('/fridge?layer=' + props.layer) }
watch(() => props.layer, load)
onMounted(load)
</script>
