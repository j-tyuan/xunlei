<script setup>
import { provide } from "vue";
import { Loading } from "@element-plus/icons-vue";

import CommandBar from "./components/CommandBar.vue";
import LogDialog from "./components/LogDialog.vue";
import PendingDialogs from "./components/PendingDialogs.vue";
import TaskQueue from "./components/TaskQueue.vue";
import TelemetryPanel from "./components/TelemetryPanel.vue";
import { bridgeKey, useThunderBridge } from "./composables/useThunderBridge";

const bridge = useThunderBridge();

provide(bridgeKey, bridge);
</script>

<template>
  <div class="bridge-app">
    <transition name="loading-fade">
      <div v-if="bridge.globalLoading.visible" class="global-loading-mask">
        <div class="global-loading-panel">
          <el-icon class="loading-icon is-loading"><Loading /></el-icon>
          <div class="loading-copy">
            <strong>正在处理</strong>
            <span>{{ bridge.globalLoading.text }}</span>
          </div>
        </div>
      </div>
    </transition>

    <header class="hero">
      <div class="hero-copy">
        <p class="eyebrow">Thunder Bridge</p>
        <h1>迅雷下载中枢</h1>
        <p class="hero-subtitle">Vue 3 + Element Plus 驱动的家庭媒体下载控制台</p>
      </div>
    </header>

    <main class="workspace">
      <section class="panel workspace-panel">
        <CommandBar />
        <TelemetryPanel />
        <TaskQueue />
      </section>
    </main>

    <PendingDialogs />
    <LogDialog />
  </div>
</template>
