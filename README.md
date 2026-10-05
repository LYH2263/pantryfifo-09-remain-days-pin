# Pantryfifo · 冰箱临期先吃

分批入库 → FEFO 扣减 → 过期下架。

| 服务 | 端口 |
| --- | --- |
| 前端 | 5300 |
| API | 10300 |

0-1：`shopping_list` / `recipe_suggest` / `temp_zone`。

## 手钉可放天数(override_days)

在架批可手钉「还能放几天」:`PUT /api/lots/{id}/override`,body `{"override_days": 正整数}`(`null` 取消钉)。
有效到期日 = 钉住日(`override_set_on`)+ 可放天数,锚定不随今天滑动;未钉批仍用入库日历 `expiry`。

一致性约定(全部走 `app/engines/expiry.py` 同一组谓词):

- 顶条 `/api/alerts`、批次详情 `/api/lots/{id}`、过期下架 `/api/expire-sweep`、消费 `/api/consume` 对同一批同一资格:日历已过期但钉了 override 的批,不下架、顶条不再 expired、全层仍在架、消费可扣。
- 全层/分层主行仍显示入库时的 `expiry`(钉值以徽标呈现),顶条紧急吃写入值。
- 钉值写入、改 `warn_days`(`PUT /api/settings`)、过期下架均走 `BEGIN IMMEDIATE` 事务互斥;`override_days` 非正数拒写(400),非在架批拒写(409),拒绝后各入口停在改前。
- `warn_days` 变基后:已钉批的消费资格与顶条紧急同看 override 基准,不会一个跟 override、一个跟新 warn。

前端:全层/分层点批次进「批次详情」(`/lots/:id`)提交天数;提交后顶条与详情由后端同一函数得出同一紧急结论。
