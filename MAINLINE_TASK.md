# Mainline Task

Current pinned mainline: Thunder Direct Download Control

## TOP0: Bypass Thunder's New Download Dialog

Goal: build a stable non-UI control path for Thunder on macOS so the web dashboard can add download tasks, inspect pending BT/magnet file lists, select files, and start downloads without being blocked by Thunder's "new download" window.

This is the highest-priority task. UI automation is only a fallback after the direct-control path is proven blocked.

## Current Findings

- Thunder runs a main app process and a private XPC process:
  - `/Applications/Thunder.app/Contents/MacOS/Thunder`
  - `/Applications/Thunder.app/Contents/XPCServices/DownloadService.xpc/Contents/MacOS/DownloadService`
- No Thunder TCP/UDP local control port is currently exposed.
- Thunder registers URL schemes: `magnet`, `ed2k`, `thunder`, `birdcmd`, `xunleiapp`, and `thunderExtension`.
- `DownloadService.xpc` exposes `DownloadServiceProtocol` with relevant methods:
  - `createTask:withReply:`
  - `getTorrentInfo:withReply:`
  - `getBTSubFileInfo:withReply:`
  - `changeBTTaskSeletion:indexList:withReply:`
  - `getAllTasksInfo:`
- `MacXLSDKs.framework` exports task dictionary keys:
  - `url`
  - `saveDirPath`
  - `fileName`
  - `fileSize`
  - `taskType`
  - `kind`
  - `source`
  - `seedPath`
  - `selectedFileIndexes`
- A normal external process cannot connect to `com.xunlei.DownloadService` by `initWithServiceName`; the connection is invalidated.
- A temporary `.app` bundle with `DownloadService.xpc` embedded can connect far enough to call `getVersionwithReply:`.
- Calling `initETM:` or `createTask:` from that temporary XPC environment currently invalidates/interrupts the XPC connection, which suggests the service needs Thunder's real initialized runtime, bundle context, or database ownership.
- `XLNewTaskNotification` is a real main-process entry:
  - Posting it through `NSDistributedNotificationCenter` can open Thunder's "new download task" window.
  - A userInfo payload with `url`, `taskURL`, `saveDirPath`, and `fileName` populates the window table and URL text area.
  - This still does not bypass the new-task window or create the task directly, so it is an intermediate control surface, not the TOP0 finish line.

## TOP0 Work Plan

1. Reverse the direct initialization path:
   - Determine how Thunder initializes `DownloadServiceProtocol`.
   - Recover the exact `initETM:` parameter shape and runtime preconditions.
   - Verify whether the service can safely run from a helper bundle without the main Thunder app.

2. Reverse the URL Scheme path:
   - Inspect `xunleiapp`, `birdcmd`, and `thunderExtension` handlers.
   - Determine whether any scheme can pass `url`, `saveDirPath`, `selectedFileIndexes`, or equivalent task metadata.
   - Prefer URL Scheme if it supports file selection because it is less invasive than XPC or process injection.

3. Reverse the notification path:
   - Determine the full `XLNewTaskNotification` payload shape.
   - Check whether there is an undocumented flag for auto-create, silent create, selected BT indexes, or panel bypass.
   - If no bypass exists, treat it as a bridge for file-list discovery only.

4. Build a dedicated macOS helper only after the control path is proven:
   - If XPC is viable, package a helper that can call `createTask:` and BT selection methods.
   - If URL Scheme is viable, package a helper around URL generation and state polling.
   - If notification is viable, package a helper around the notification payload and direct confirmation path.
   - Keep the web dashboard API unchanged where possible.

5. Keep UI automation as fallback only:
   - The current AppleScript click-based approach is not the TOP0 target.
   - Do not invest further in UI list scraping unless direct XPC/URL paths are conclusively blocked.

## Success Criteria

- Web dashboard can submit a URL or magnet.
- For BT/magnet resources, dashboard can show the file list before download starts.
- User can select one or more files from the dashboard.
- Mac starts the Thunder download without manual interaction with the Thunder pop-up.
- The flow survives Thunder restart and does not depend on fixed screen coordinates.
