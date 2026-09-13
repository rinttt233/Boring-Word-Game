# 盲玩测试汇总（policy=suggest，seed 100~101）

| seed | 结果 | 用时(分) | 覆盖 | 设施 | 单元(效能) | 固化 | DB | 记忆 | 停摆(设施·秒) | 墙钟(s) |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 | timeout | 300.2 | 12/22 | 1 | 5(×1.25) | 5 | 0/5 | 0.0 | 3304.0 | 0.8 |
| 101 | timeout | 302.7 | 11/22 | 23 | 4(×1.0) | 1 | 0/5 | 0.0 | 395704.0 | 0.7 |

## 未覆盖项

- seed 100：fuel、chain_steel、chem、byproduct、renewable、env_event、maintenance、mothball、db_build、victory
- seed 101：fuel、maintain、efficiency、chem、byproduct、renewable、env_event、maintenance、mothball、db_build、victory
