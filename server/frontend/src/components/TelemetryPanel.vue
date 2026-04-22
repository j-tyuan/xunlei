<script setup>
import { inject } from "vue";

import { bridgeKey } from "../composables/useThunderBridge";

const bridge = inject(bridgeKey);
</script>

<template>
  <section class="workspace-section telemetry-layout">
    <article class="speed-panel telemetry-panel">
      <div class="speed-value">{{ bridge.speedText }}</div>
      <div class="speed-summary">
        <span class="speed-summary-item">{{ bridge.lastUpdateText }}</span>
        <span class="speed-summary-item">下载中: {{ bridge.stats.activeTasks ?? 0 }}</span>
        <span class="speed-summary-item">已完成: {{ bridge.stats.completedTasks ?? 0 }}</span>
        <span class="speed-summary-item">任务总数: {{ bridge.stats.totalTasks ?? 0 }}</span>
        <span class="speed-summary-item">Mac状态: {{ bridge.macStateText }}</span>
        <span class="speed-summary-item speed-summary-status" :title="bridge.connectionLabel" :aria-label="bridge.connectionLabel">
          <span class="agent-led" :class="`is-${bridge.connectionTone}`"></span>
          <span>代理</span>
        </span>
      </div>
    </article>
  </section>
</template>
