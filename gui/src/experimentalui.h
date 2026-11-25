#pragma once
// One compact disclosure for an advanced / rarely used section: the caller adds its panel right after it.
#include <QSizePolicy>
#include <QString>
#include <QToolButton>
#include <QWidget>

namespace experimentalui {

inline QToolButton *disclosure(const QString &text, QWidget *panel, bool expanded = false) {
    auto *toggle = new QToolButton;
    toggle->setObjectName(QStringLiteral("expDisclosure"));
    toggle->setText(text);
    toggle->setToolButtonStyle(Qt::ToolButtonTextBesideIcon);
    toggle->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    toggle->setArrowType(expanded ? Qt::DownArrow : Qt::RightArrow);
    toggle->setCheckable(true);
    toggle->setChecked(expanded);
    panel->setVisible(expanded);
    QObject::connect(toggle, &QToolButton::toggled, panel, [panel, toggle](bool on) {
        panel->setVisible(on);
        toggle->setArrowType(on ? Qt::DownArrow : Qt::RightArrow);
    });
    return toggle;
}

}  // namespace experimentalui
