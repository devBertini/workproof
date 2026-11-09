---
name: laying-out-buttons
description: "Places and styles the buttons of a screen: the order of confirming and cancelling actions per platform, one prominent action, destructive never the default, hit targets. Use when building or reviewing a screen, dialog, alert, form, wizard, toolbar or game menu that has buttons."
---

# Laying out buttons

If `${user_config.ui_conventions}` is false, this module is off: ignore this skill.

## Order

Positive actions are confirm, save, next, finish, send, buy, allow, continue and the destructive action itself (Delete, Discard). Negative actions are cancel, back, previous, close, not now and block.

- **Apple platforms and the web (default):** positive on the trailing side, negative on the leading side. With a right-to-left language the whole row mirrors.
- **Windows:** the platform's own dialogs put the affirmative button first (leading), and a WinUI `ContentDialog` does so with its built-in buttons. When `${user_config.buttons_positive_trailing_everywhere}` is true, Windows follows the trailing rule too: a `ContentDialog` then gets its own button row in that order instead of the built-in Primary and Close buttons, and keeps their behavior (Enter on the default, Esc and the back button on the cancel). When it is false, follow the platform.
- **A vertical stack** (a phone's bottom actions, an action sheet): the most likely action on top, Cancel at the bottom.
- **A wizard or multi-step form:** Back at the leading edge, Next (on the last step, the verb of the result: "Create", "Pay") at the trailing edge; Cancel, when present, beside Back on the leading side, never between them.
- **Navigation and tool bars:** back and Cancel at the top leading corner, Done, Save or Add at the top trailing corner.
- **Three buttons:** the most likely at the positive edge, the alternative ("Try it", "Save draft") next to it, the cancel at the other edge, separated from the two by space.
- **A game menu** uses the same order for confirm and back, and the same sides for the controller's confirm and back where the screen shows both.

## Emphasis, color and size

- One prominent button (filled with the accent color) per view, at most two; cancel and back use the plain or secondary style. A set of alternatives uses buttons of the same size and height; the preferred one differs by style, not by size.
- A destructive button uses the system red (the project's danger token), is never the prominent or default button and never answers Enter: a Cancel sits beside it. When the person deliberately chose the destructive action (Empty Trash), the confirming button may stay default and unstyled.
- The default button answers Enter or Return and Cancel answers Esc; Cancel is never the default. Labels are specific verbs ("Delete", "Save", "Allow"); "Cancel" stays "Cancel"; "OK" only for a purely informational alert.
- A button busy with work keeps its place and size and shows its progress inside it, with a label of what is happening.

## Space and target

- Hit area at least 44x44 pt on iPhone, iPad and Mac touch targets (60x60 pt on visionOS); on the web at least 24x24 CSS px (WCAG 2.2, 2.5.8), 44x44 px for touch screens. An image-only button keeps its padding inside the hit area.
- The gap between paired buttons and from the container's edges comes from the project's spacing tokens (or the platform's standard spacing), the same in every dialog of the product; the pair aligns to its dialog's or form's edge, not centered, unless the buttons span the full width.
- Small and mini sizes do not go in a row or stack of actions.
- A dialog's buttons sit at its bottom; a form's at the end of the form, not floating in the middle; a phone's main action within thumb reach at the bottom; a desktop window's at the bottom corner.

## Proof

A screen with buttons is checked on its capture (light and dark, each supported language, the smallest and largest supported size): order, one prominent action, destructive never default, Enter and Esc wired.

Sources: developer.apple.com/design/human-interface-guidelines/alerts and /buttons; learn.microsoft.com/en-us/windows/apps/develop/ui/controls/dialogs-and-flyouts/dialogs; w3.org/WAI/WCAG22/Understanding/target-size-minimum.
