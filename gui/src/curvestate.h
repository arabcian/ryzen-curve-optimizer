#pragma once
// Which curve is in effect right now: the last line of this boot in the root helper's curve journal
// (/var/lib/ryzen-curve-optimizer/oc-journal.jsonl). A reboot resets the Curve Optimizer, so only the
// current boot's lines count. "stock", "profile:NAME", "custom", "per-core", "all-core N", or "external" after a more recent Centurion write.
#include <QString>

namespace curvestate {
/// `group` is "cpu" (the only curve this app manages); anything else is always "stock".
QString current(const QString &group);
}
