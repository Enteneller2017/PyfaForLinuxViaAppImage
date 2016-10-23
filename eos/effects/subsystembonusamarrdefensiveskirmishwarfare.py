# subSystemBonusAmarrDefensiveSkirmishWarfare
#
# Used by:
# Subsystem: Legion Defensive - Warfare Processor
type = "passive"
def handler(fit, module, context):
    fit.modules.filteredItemBoost(lambda mod: mod.item.requiresSkill("Skirmish Command Specialist"),
                                  "commandBonus", module.getModifiedItemAttr("subsystemBonusAmarrDefensive"), skill="Amarr Defensive Systems")
