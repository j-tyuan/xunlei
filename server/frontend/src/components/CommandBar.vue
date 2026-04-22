<script setup>
import { inject } from "vue";
import { DeleteFilled, Link, Promotion } from "@element-plus/icons-vue";

import { bridgeKey } from "../composables/useThunderBridge";

const bridge = inject(bridgeKey);
</script>

<template>
  <section class="workspace-section command-panel">
    <div class="command-copy">
      <p class="eyebrow">Create Task</p>
      <h2>新建下载</h2>
      <p class="command-subtitle">支持 `magnet / ed2k / thunder / http`。需要文件选择的任务会在右上角弹出待确认窗口。</p>
    </div>

    <div class="command-actions">
      <el-input
        v-model="bridge.taskUrl"
        class="task-url-input"
        size="large"
        clearable
        placeholder="magnet / http / ed2k / thunder 链接"
        @keyup.enter="bridge.submitTask"
      >
        <template #prefix>
          <el-icon><Link /></el-icon>
        </template>
      </el-input>

      <el-button type="primary" size="large" :loading="bridge.addBusy" @click="bridge.submitTask">
        <el-icon><Promotion /></el-icon>
        <span>继续处理</span>
      </el-button>

      <el-button type="danger" plain size="large" :loading="bridge.cleanupBusy" @click="bridge.cleanupDownloads">
        <el-icon><DeleteFilled /></el-icon>
        <span>清空下载目录</span>
      </el-button>
    </div>

    <div class="command-feedback">
      <span class="message-line" :class="bridge.addMessage.type">
        {{ bridge.addMessage.text || "支持直接创建普通下载任务，也支持先预览文件再确认下载。" }}
      </span>
      <span class="message-line" :class="bridge.cleanupMessage.type">
        {{ bridge.cleanupMessage.text }}
      </span>
    </div>
  </section>
</template>
