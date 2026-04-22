<script setup>
import { inject } from "vue";

import { bridgeKey } from "../composables/useThunderBridge";

const bridge = inject(bridgeKey);
</script>

<template>
  <section v-if="bridge.pendingDialogs.length" class="pending-stack">
    <article
      v-for="(pending, index) in bridge.pendingDialogs"
      :key="pending.id"
      class="pending-dialog"
      :style="bridge.pendingDialogStyle(pending, index)"
      @pointerdown="bridge.bringPendingToFront(pending.id)"
    >
      <div class="pending-shell">
        <div class="pending-head" @pointerdown="(event) => bridge.startPendingDrag(pending, index, event)">
          <div>
            <p class="eyebrow">Pending Dialog</p>
            <h3>{{ pending.name || "待确认任务" }}</h3>
            <p class="pending-summary">{{ pending.summary || "已获取文件列表，请勾选后再开始下载。" }}</p>
          </div>
          <div class="pending-meta">
            <strong>{{ bridge.pendingSelectedCount(pending) }}/{{ (pending.files || []).length }}</strong>
            <span>已勾选</span>
          </div>
        </div>

        <div class="pending-toolbar">
          <div class="inline-actions">
            <el-button link type="primary" :disabled="bridge.getPendingUi(pending.id).busy" @click="bridge.selectAllPending(pending)">
              全选
            </el-button>
            <el-button link :disabled="bridge.getPendingUi(pending.id).busy" @click="bridge.clearPendingSelection(pending.id)">
              清空
            </el-button>
          </div>

          <div class="inline-actions">
            <el-button
              plain
              :disabled="bridge.getPendingUi(pending.id).busy"
              @click="bridge.cancelPending(pending)"
            >
              取消
            </el-button>
            <el-button
              type="primary"
              :loading="bridge.getPendingUi(pending.id).busy"
              :disabled="bridge.pendingSelectedCount(pending) === 0 || bridge.getPendingUi(pending.id).busy"
              @click="bridge.confirmPending(pending)"
            >
              勾选后下载
            </el-button>
          </div>
        </div>

        <div class="pending-table-head">
          <span>文件</span>
          <span>类型</span>
          <span>大小</span>
        </div>

        <el-scrollbar class="pending-file-list">
          <div v-if="!(pending.files || []).length" class="pending-empty">还没有拿到文件列表，请稍等。</div>

          <label v-for="file in pending.files || []" :key="file.id" class="pending-file-row">
            <el-checkbox
              :model-value="bridge.isPendingSelected(pending.id, file.id)"
              :disabled="bridge.getPendingUi(pending.id).busy"
              @change="(checked) => bridge.togglePendingSelection(pending.id, file.id, checked)"
            >
              <span class="pending-file-name">{{ file.name || `文件 ${file.id}` }}</span>
            </el-checkbox>
            <span class="pending-type">{{ file.type || "-" }}</span>
            <span class="pending-size">{{ file.sizeText || "-" }}</span>
          </label>
        </el-scrollbar>

        <div class="pending-footer">
          <span class="message-line" :class="bridge.getPendingUi(pending.id).type">
            {{ bridge.getPendingUi(pending.id).message || "选择文件后开始下载。" }}
          </span>
        </div>
      </div>
    </article>
  </section>
</template>
