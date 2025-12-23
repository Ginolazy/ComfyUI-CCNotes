import { app } from "../../scripts/app.js";

/**
 * FluxKontext Node Extension
 * Handles dynamic widget visibility (e.g., solid_color input)
 */

app.registerExtension({
    name: "CCNotes.FluxKontext",

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "FluxKontextImageCompensate") {
            const onNodeCreated = nodeType.prototype.onNodeCreated;

            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;

                const node = this;

                // Store the hidden widget separately so we can restore it
                this.hiddenWidgets = {};

                // Function to update visibility
                this.updateVisibility = function () {
                    const paddingWidget = node.widgets.find(w => w.name === "comp_mode");
                    if (!paddingWidget) return;

                    const isSolidColor = paddingWidget.value === "Solid Color";
                    const targetWidgetName = "solid_color";

                    if (isSolidColor) {
                        // SHOW
                        // If it's in our hidden stash, put it back
                        if (this.hiddenWidgets[targetWidgetName]) {
                            const w = this.hiddenWidgets[targetWidgetName];
                            // Find correct insertion point (usually after comp_mode)
                            const padIdx = node.widgets.findIndex(w => w.name === "comp_mode");
                            if (padIdx !== -1) {
                                node.widgets.splice(padIdx + 1, 0, w);
                            } else {
                                node.widgets.push(w);
                            }
                            delete this.hiddenWidgets[targetWidgetName];
                        }
                    } else {
                        // HIDE
                        // Find current widget
                        const widgetIdx = node.widgets.findIndex(w => w.name === targetWidgetName);
                        if (widgetIdx !== -1) {
                            // Save it
                            this.hiddenWidgets[targetWidgetName] = node.widgets[widgetIdx];
                            // Remove it
                            node.widgets.splice(widgetIdx, 1);
                        }
                    }

                    // Force resize
                    node.onResize && node.onResize(node.size);
                    const sz = node.computeSize();
                    // Keep the current width if it is larger than the computed size (user might have resized)
                    node.setSize([Math.max(sz[0], node.size[0]), sz[1]]);
                    node.setDirtyCanvas(true, true);
                };

                // Finds widgets and Setup Callback
                setTimeout(() => {
                    const paddingWidget = node.widgets.find(w => w.name === "comp_mode");
                    if (paddingWidget) {
                        const originalCallback = paddingWidget.callback;
                        paddingWidget.callback = (value) => {
                            node.updateVisibility();
                            if (originalCallback) {
                                originalCallback.call(paddingWidget, value);
                            }
                        };
                        // Initial update
                        node.updateVisibility();
                    }
                }, 50);

                return r;
            };
        }
    }
});
