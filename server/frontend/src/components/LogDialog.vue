<script setup>
import { inject } from "vue";

import { bridgeKey } from "../composables/useThunderBridge";

const bridge = inject(bridgeKey);
</script>

<template>
  <el-dialog
    v-model="bridge.logDialogVisible"
    title="Thunder Bridge 日志"
    class="log-dialog"
    width="1080px"
    top="6vh"
    append-to-body
    draggable
  >
    <pre class="log-console">{{ bridge.logText }}</pre>
  </el-dialog>
</template>
