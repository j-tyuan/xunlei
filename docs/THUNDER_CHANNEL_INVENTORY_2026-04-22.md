# Thunder Channel Inventory - 2026-04-22

This file records the current Thunder macOS channel inventory for the TOP0 direct-control work.

## Boundary

The project may automate only the user's normal local Thunder workflow:

- create normal download tasks from user-provided URLs or magnets
- inspect normal task status and BT file metadata
- start, stop, delete, or migrate local tasks only when those actions match normal user-visible controls
- read passive task counters from the user's local database

The project must not invoke, unlock, patch, emulate, or test premium acceleration, VIP, cloud privilege, offline privilege, or access-control bypass paths.

## Observed Normal-Control Candidates

These paths are candidates for normal download automation:

- `Thunder.BaseHostController.createTask:completion:`
  - Runtime selector type: `v32@0:8@16@?24`
  - Static disassembly shows it reads normal task keys such as `url`, `entryId`, `fileName`, `fileSize`, `saveDirPath`, and `userAgent`.
  - It appears to construct a normal URL task context and pass it into the download session path.
  - This is the main candidate for bypassing the "new download task" dialog.
- `Thunder.BaseHostController.newPanelTasks:options:completion:`
  - Runtime selector type: `v40@0:8@16@24@?32`
  - This is a UI panel path, not the target direct-control path.
- `Thunder.BaseHostController.getTaskSubTasks:callbackQueue:completion:`
  - Candidate normal metadata path for BT/magnet file selection.
- `Thunder.BaseHostController.startTasks:completionHandler:`
  - Candidate normal task action.
- `Thunder.BaseHostController.stopTasks:completionHandler:`
  - Candidate normal task action.
- `Thunder.BaseHostController.deleteTasks:deleteFile:completionHandler:`
  - Candidate normal cleanup action.
- `Thunder.BaseHostController.allTasks`
  - Candidate normal task listing path.
- `Thunder.BaseHostController.downloadingTasks`
  - Candidate normal task listing path.
- `DownloadService.xpc` protocol methods:
  - `createTask:withReply:`
  - `getTorrentInfo:withReply:`
  - `getBTSubFileInfo:withReply:`
  - `changeBTTaskSeletion:indexList:withReply:`
  - `getAllTasksInfo:`
  - External helper access is not yet stable because direct XPC calls invalidate or interrupt after some methods.

## UI or Bridge-Only Paths

These paths are useful for discovery or fallback, but they are not the TOP0 target:

- `XLNewTaskNotification`
  - Opens and populates the Thunder "new download task" window.
  - Does not bypass the window in current tests.
- `/Applications/Thunder.app/Contents/PlugIns/ChromeExtension`
  - Static strings show it receives browser native messages and forwards them into `XLNewTaskNotification`.
  - Current classification: browser bridge to the normal new-task UI, not direct normal task creation.

## Passive Data Channels

The local task database table `etm_task` contains these passive byte counters:

- `origin_bytes`
- `server_bytes`
- `p2p_Bytes`
- `dcdn_Bytes`

Current passive DB risk test:

- Database path: `~/Library/Application Support/Thunder/etm3/etm_map.db`
- Table inspected: `etm_task`
- The schema contains the four counters above.
- No current task rows were present during the 2026-04-22 probe, so no live channel usage sample was available.

These counters are read-only observability fields. They do not authorize changing download capability.

## Red-Line and High-Risk Paths

These paths are not implementation targets. They may be documented for boundary awareness, but must not be invoked by this project.

- `Thunder.BaseHostController._accelerationService`
  - Runtime ivar offset: `16`
  - Classification: acceleration/VIP risk boundary.
- `Thunder.BaseHostController.setTaskAccelerateToken:token:accelerateType:subIndex:completion:`
  - Classification: explicit acceleration-token path. Do not call.
- `Thunder.BaseHostController.removeTaskAccelerateToken:subIndex:`
  - Classification: explicit acceleration-token path. Do not call.
- `Thunder.BaseHostController.showXunLeiDownloadEquityAlertWith:`
  - Classification: user-facing download-equity/VIP path. Do not automate.
- `Thunder.BaseHostController.showXunLeiDownloadEquityFeaturesAlertWith:from:`
  - Classification: user-facing download-equity/VIP path. Do not automate.
- `AccelerationKit`
  - Observed symbols include `AccelerationKit.Service`, `Token`, `TaskAccelerateDelegate`, `TrialRequestServiceType`, and `TaskTrialAccelerateDelegate`.
  - Classification: premium/acceleration subsystem. Do not call.
- `Thunder.DownloadSession`
  - Normal session class, but it contains acceleration-related state:
    - `_localAccelerationSpeed`
    - `_speedupTasksCount`
    - `$__lazy_storage_$_accelerateService`
    - `_trialPackService`
  - Classification: the class itself can own normal task state, but acceleration members are red-line members.
- `Thunder.VIPTaskViewModel`
  - Observed ivars include `_accelerationService`, `_trialPackService`, `_isSpeedup`, `_tokens`, and `downloadTask`.
  - Classification: VIP/acceleration UI/model. Do not call.
- `Thunder.ZeroSpeedService`
  - Observed ivars include access-token and provider fields.
  - Classification: privilege/anti-zero-speed service. Do not call.
- `MacXLSDKs` VIP/high-speed fields:
  - `DSKeyTaskResInfoHIGHSPEEDBytes`
  - `DSKeyTaskResInfoVipBytes`
  - `DSKeyVIPTaskInfoDonwloadSpeed`
  - `DSKeyVIPTaskInfoDownloadDataSize`
  - `DSKeyVIPTaskInfoErrorCode`
  - `DSKeyVIPTaskInfoState`
  - Classification: observe-only if present in normal metadata; never use to request capability.

## Cloud and Offline Privilege Paths

These paths may be legitimate Thunder features, but they are outside this household media automation target unless explicitly reviewed separately:

- `Thunder.BaseHostController.fetchOfflineTaskLimitWithCompletion:`
- `Thunder.BaseHostController.playWithCloudFile:option:`
- `Thunder.BaseHostController.showXunLeiCloudPathChooseAlertForParentIdBlock:`
- `Thunder.CloudPrivilegeService`
- `Thunder.CloudSpaceService`
- `Thunder.CreateTaskController` cloud/offline fields:
  - `_cloudSpaceService`
  - `remainingOfflineTaskCount`
  - `offlineTaskUsageCount`
  - `cloudBoxBT`
  - `cloudPathChooseBT`
- `Thunder.CreateBTTaskWindowController` cloud/offline fields:
  - `_cloudSpaceService`
  - `remainingOfflineTaskCount`
  - `offlineTaskUsageCount`
  - `$__lazy_storage_$_isCloudTask`
  - `cloudBoxBT`
  - `cloudPathChooseBT`

Classification: do not automate in this project.

## Safe Risk-Test Strategy

Risk testing should prove that the normal candidate path does not enter red-line methods.

Allowed tests:

- Static call-path review around `BaseHostController.createTask:completion:`.
- Runtime introspection of class names, ivars, and selectors.
- Passive local database reads.
- Breakpoint-only negative test using a small user-owned HTTP file:
  - set breakpoints/logpoints on `createTask:completion:`
  - set breakpoints/logpoints on red-line methods such as `setTaskAccelerateToken`, `removeTaskAccelerateToken`, `showXunLeiDownloadEquity*`, and `fetchOfflineTaskLimitWithCompletion:`
  - trigger only a normal local HTTP download
  - confirm whether any red-line breakpoint is hit

Not allowed:

- directly invoking red-line methods
- supplying fake acceleration tokens
- modifying Thunder binaries, signatures, accounts, or task database rows
- testing with third-party copyrighted content

