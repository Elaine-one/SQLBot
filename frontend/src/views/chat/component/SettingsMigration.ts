/**
 * SettingsMigration — 对标 Metabase 的 onDisplayUpdate
 *
 * 当 settings schema 变更时，自动迁移用户保存的旧设置到新格式。
 * 在 ChartBlock 加载 localStorage 时调用。
 */

export interface SettingMigration {
  /** 迁移名称，用于日志 */
  name: string
  /** 适用图表类型（空=所有） */
  appliesTo?: string[]
  /** 迁移函数：接收旧 settings，返回新 settings */
  migrate: (settings: Record<string, any>, chartType: string) => Record<string, any>
}

// ═══════════════════════════════════════════════════════════════════════════════
//  迁移列表（按时间顺序追加）
// ═══════════════════════════════════════════════════════════════════════════════

const MIGRATIONS: SettingMigration[] = [
  // 示例：未来 rename 某个 key 时添加
  // {
  //   name: 'rename_stack_to_stack_type',
  //   appliesTo: ['column', 'bar'],
  //   migrate(settings) {
  //     if ('stack' in settings && !('stack_type' in settings)) {
  //       settings.stack_type = settings.stack ? 'stacked' : null
  //       delete settings.stack
  //     }
  //     return settings
  //   },
  // },
]

// ═══════════════════════════════════════════════════════════════════════════════
//  清理废弃 key（chart_registry.py 不再下发的 key）
// ═══════════════════════════════════════════════════════════════════════════════

const DEPRECATED_KEYS_BY_TYPE: Record<string, string[]> = {
  // 未来某个版本删除旧 key 时添加
  // column: ['old_setting_key'],
}

// ═══════════════════════════════════════════════════════════════════════════════
//  入口：在 initSettingsFromDefaults 前调用
// ═══════════════════════════════════════════════════════════════════════════════

export function migrateSettings(
  settings: Record<string, any>,
  chartType: string,
): Record<string, any> {
  let migrated = { ...settings }

  // 1. 应用结构化迁移
  for (const m of MIGRATIONS) {
    if (m.appliesTo && !m.appliesTo.includes(chartType)) continue
    migrated = m.migrate(migrated, chartType)
    console.log(`[Migration] ${m.name} applied for ${chartType}`)
  }

  // 2. 清理废弃 key
  const deprecated = DEPRECATED_KEYS_BY_TYPE[chartType]
  if (deprecated) {
    for (const key of deprecated) {
      if (key in migrated) {
        delete migrated[key]
        console.log(`[Migration] removed deprecated key "${key}" from ${chartType}`)
      }
    }
  }

  return migrated
}
