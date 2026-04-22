<script setup>
import { computed, inject, ref } from "vue";

import { bridgeKey } from "../composables/useThunderBridge";

const bridge = inject(bridgeKey);
const activeTab = ref("downloading");

const tabItems = computed(() => {
  const allTasks = bridge.tasks || [];
  const downloadingTasks = allTasks.filter((task) => task?.status !== "completed");
  const completedTasks = allTasks.filter((task) => task?.status === "completed");

  return [
    {
      name: "downloading",
      label: "正在下载",
      emptyText: "暂无正在处理的任务",
      tasks: downloadingTasks,
    },
    {
      name: "completed",
      label: "已完成",
      emptyText: "暂无已完成任务",
      tasks: completedTasks,
    },
  ];
});
</script>

<template>
  <section class="workspace-section queue-panel">
    <div class="section-head section-head-plain">
      <el-button link type="primary" @click="bridge.logDialogVisible = true">查看日志</el-button>
    </div>

    <el-tabs v-model="activeTab" class="queue-tabs">
      <el-tab-pane v-for="tab in tabItems" :key="tab.name" :name="tab.name">
        <template #label>
          <span class="task-tab-label">
            <span>{{ tab.label }}</span>
            <small>{{ tab.tasks.length }}</small>
          </span>
        </template>

        <el-empty v-if="!tab.tasks.length" :description="tab.emptyText" />

        <div v-else class="task-list">
          <article v-for="task in tab.tasks" :key="`${tab.name}-${task.id}`" class="task-row">
            <div class="task-row-head">
              <div class="task-name-block">
                <h3>{{ task.name || `任务 ${task.id}` }}</h3>
                <p>{{ task.path || "-" }}</p>
              </div>
              <el-tag :type="bridge.statusTagType(task.status)" effect="dark">{{ bridge.statusText(task.status) }}</el-tag>
            </div>

            <el-progress
              :percentage="bridge.taskProgress(task)"
              :stroke-width="10"
              :show-text="false"
              :color="bridge.progressGradient"
              striped
              striped-flow
            />

            <div class="task-meta">
              <span>{{ bridge.taskProgress(task).toFixed(1) }}%</span>
              <span>{{ bridge.formatBytes(task.downloaded) }} / {{ bridge.formatBytes(task.totalSize) }}</span>
              <span>{{ bridge.formatSpeed(task.speed) }}</span>
              <el-tag
                v-if="bridge.manualTaskFiles(task).length"
                type="warning"
                effect="plain"
                size="small"
              >
                多文件任务需手动迁移
              </el-tag>
            </div>

            <div class="task-actions">
              <el-button
                v-if="bridge.canPauseTask(task) || (bridge.getTaskUi(task.id).busy && bridge.getTaskUi(task.id).action === 'pause')"
                link
                type="warning"
                :loading="bridge.getTaskUi(task.id).busy && bridge.getTaskUi(task.id).action === 'pause'"
                @click="bridge.controlTask(task, 'pause')"
              >
                {{ bridge.taskActionLabel("pause", bridge.getTaskUi(task.id).busy && bridge.getTaskUi(task.id).action === "pause") }}
              </el-button>

              <el-button
                v-if="bridge.canStartTask(task) || (bridge.getTaskUi(task.id).busy && bridge.getTaskUi(task.id).action === 'start')"
                link
                type="primary"
                :loading="bridge.getTaskUi(task.id).busy && bridge.getTaskUi(task.id).action === 'start'"
                @click="bridge.controlTask(task, 'start')"
              >
                {{ bridge.taskActionLabel("start", bridge.getTaskUi(task.id).busy && bridge.getTaskUi(task.id).action === "start") }}
              </el-button>

              <el-button
                v-if="bridge.canDeleteTask(task)"
                link
                type="danger"
                :loading="bridge.getTaskUi(task.id).busy && bridge.getTaskUi(task.id).action === 'delete'"
                @click="bridge.controlTask(task, 'delete')"
              >
                {{ bridge.taskActionLabel("delete", bridge.getTaskUi(task.id).busy && bridge.getTaskUi(task.id).action === "delete") }}
              </el-button>
            </div>

            <div v-if="bridge.getTaskUi(task.id).message" class="task-note" :class="bridge.getTaskUi(task.id).type">
              {{ bridge.getTaskUi(task.id).message }}
            </div>

            <div v-if="bridge.manualTaskFiles(task).length" class="file-list">
              <div class="file-list-head">
                <span>选择要迁移的文件</span>
                <small>{{ bridge.manualTaskFiles(task).length }} 个文件</small>
              </div>

              <div v-for="file in bridge.manualTaskFiles(task)" :key="file.path || file.name" class="file-row">
                <div class="file-copy">
                  <span class="file-name">{{ file.name || "未命名文件" }}</span>
                  <small>{{ bridge.formatBytes(file.size) }}</small>
                </div>
                <div class="file-actions">
                  <el-button
                    link
                    type="primary"
                    :disabled="bridge.getFileUi(file.path).done"
                    :loading="bridge.getFileUi(file.path).busy && bridge.getFileUi(file.path).action === 'migrate'"
                    @click="bridge.migrateFile(file)"
                  >
                    {{ bridge.getFileUi(file.path).action === "migrate" && bridge.getFileUi(file.path).busy ? "迁移中..." : bridge.getFileUi(file.path).label || "迁移" }}
                  </el-button>
                  <el-button
                    link
                    type="danger"
                    :disabled="bridge.getFileUi(file.path).done"
                    :loading="bridge.getFileUi(file.path).busy && bridge.getFileUi(file.path).action === 'delete'"
                    @click="bridge.deleteFile(file)"
                  >
                    {{ bridge.getFileUi(file.path).action === "delete" && bridge.getFileUi(file.path).busy ? "删除中..." : (bridge.getFileUi(file.path).done && bridge.getFileUi(file.path).action === "delete" ? "已删除" : "删除") }}
                  </el-button>
                </div>
              </div>
            </div>
          </article>
        </div>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>
