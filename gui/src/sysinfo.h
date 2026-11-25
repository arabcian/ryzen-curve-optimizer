#pragma once
// The two hardware identity strings the curve tools need: CPU model and DMI fields (sysfs / procfs only).
#include <QString>
#include <optional>

namespace sysinfo {
using Opt = std::optional<QString>;
/// Raw /sys/class/dmi/id/<field>, trimmed; empty when missing or unreadable.
Opt dmi(const QString &field);
/// "model name" of the first processor in /proc/cpuinfo.
Opt cpuModel();
}
