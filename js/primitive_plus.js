/** ComfyUI/custom_nodes/CCNotes/js/primitive_plus.js **/
import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

app.registerExtension({
    name: "PrimitivePlus",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "PrimitivePlus") {
            const origOnNodeCreated = nodeType.prototype.onNodeCreated;
            const origOnConnectionsChange = nodeType.prototype.onConnectionsChange;
            const origSerialize = nodeType.prototype.serialize;
            const origConfigure = nodeType.prototype.configure;

            // --- Initialization ---
            nodeType.prototype.onNodeCreated = function () {
                if (origOnNodeCreated) origOnNodeCreated.apply(this, arguments);

                // Initial Port Setup
                if (!this.outputs || this.outputs.length === 0) {
                    this.addOutput("connect_to_widget_input_1", "*");
                } else if (this.outputs.length > 1) {
                    // Fix Flash on Creation:
                    // Python def has 10 ports. Truncate strictly to 1 for clean start.
                    this.outputs.length = 1;
                }

                // Initialize internal state
                this.localWidgets = {};

                // Track current connected node/slot for each output to detect changes
                this.connectionState = {};

                // Properties for persistence
                this.properties = this.properties || {};
                this.properties.portLabels = this.properties.portLabels || {};

                this.size = [this.size[0], 32]; // Initial size

                // Monitor size changes to update label truncation
                const origOnResize = this.onResize;
                this.onResize = function (newSize) {
                    if (origOnResize) origOnResize.apply(this, arguments);
                    this.updateLabelsForWidth();
                };

                // Immediately manage ports to ensure clean start (trim excess or add defaults)
                setTimeout(() => {
                    this.managePorts();
                }, 10);
            };

            // --- Serialization ---
            nodeType.prototype.serialize = function () {
                const o = origSerialize ? origSerialize.apply(this, arguments) : {};
                if (o.widgets) {
                    // Filter out our dynamic widgets from synthesis
                    o.widgets = o.widgets.filter(w => !w.name || !w.name.startsWith("connect_to_widget_input_"));
                    if (o.widgets.length === 0) {
                        delete o.widgets;
                    }
                }

                // Return the serialized object!
                return o;
            };

            // --- Configuration / Restore ---
            nodeType.prototype.configure = function (o) {
                if (origConfigure) origConfigure.apply(this, arguments);

                // Fix Flash/Ghost Ports:
                // Python def creates 10 ports. Saved state might have fewer.
                // Truncate extra ports immediately to prevent 10-port render flash.
                if (o.outputs && this.outputs && this.outputs.length > o.outputs.length) {
                    this.outputs.length = o.outputs.length;
                }

                if (this.widgets) {
                    // removing widgets in place carefully
                    for (let i = this.widgets.length - 1; i >= 0; i--) {
                        if (this.widgets[i].name && this.widgets[i].name.startsWith("connect_to_widget_input_")) {
                            this.widgets.splice(i, 1);
                        }
                    }
                }

                if (this.properties.portLabels) {
                    for (let i = 0; i < this.outputs.length; i++) {
                        if (this.properties.portLabels[i]) {
                            this.outputs[i].label = this.truncateLabelForWidth(this.properties.portLabels[i]);
                        }
                    }
                }

                // Defer reconstruction to ensure connections are ready (LiteGraph heuristic)
                setTimeout(() => {
                    this.managePorts();
                    this.refreshWidgets();
                }, 50);
            };

            // --- Port Management ---
            nodeType.prototype.managePorts = function () {
                // Policy: Always have exactly ONE unconnected output at the bottom.
                // Also, ensure Port 1 exists.

                if (!this.outputs) this.outputs = [];

                // 1. Identify connected ports
                let lastConnectedIndex = -1;
                for (let i = 0; i < this.outputs.length; i++) {
                    if (this.isOutputConnected(i)) {
                        lastConnectedIndex = i;
                    }
                }

                // 2. We need ports up to lastConnectedIndex + 1 (the empty one)
                const neededPorts = lastConnectedIndex + 2; // e.g. if 0 is connected, we need index 0 and 1. so 2 ports.

                // 3. Add missing ports
                while (this.outputs.length < neededPorts) {
                    const idx = this.outputs.length + 1;
                    this.addOutput(`connect_to_widget_input_${idx}`, "*");
                }

                // 4. Remove excess ports (from the end)
                // But NEVER remove if it causes total ports < 1
                while (this.outputs.length > neededPorts && this.outputs.length > 1) {
                    const removeIdx = this.outputs.length - 1;
                    // Clean up properties for the removed port to prevent stale labels on reconnect
                    if (this.properties.portLabels) {
                        delete this.properties.portLabels[removeIdx];
                    }
                    this.removeOutput(removeIdx);
                }

                // 5. Restore labels if needed (though addOutput/removeOutput shift things?)
                // Actually LiteGraph shifts links on removeOutput(i), so if we remove from end it's safe.

                // Verify labels match properties (use truncated version for comparison and assignment)
                for (let i = 0; i < this.outputs.length; i++) {
                    if (this.properties.portLabels[i]) {
                        const truncatedLabel = this.truncateLabelForWidth(this.properties.portLabels[i]);
                        if (this.outputs[i].label !== truncatedLabel) {
                            this.outputs[i].label = truncatedLabel;
                        }
                    }
                }
            };

            // --- Widget Reconstruction ---
            nodeType.prototype.refreshWidgets = function () {
                // 1. Cleanup orphaned widgets (from removed ports)
                // This is critical because managePorts destroys the port but doesn't know about the widget.
                if (this.widgets) {
                    for (let i = this.widgets.length - 1; i >= 0; i--) {
                        const w = this.widgets[i];
                        if (w.name && w.name.startsWith("connect_to_widget_input_")) {
                            const portExists = this.outputs && this.outputs.some(o => o.name === w.name);
                            if (!portExists) {
                                this.removeLocalWidget(w.name);
                            }
                        }
                    }
                }

                // 2. Rebuilds widgets based on CURRENT connections.

                // Iterate all outputs
                for (let i = 0; i < this.outputs.length; i++) {
                    const output = this.outputs[i];
                    const outputName = output.name;

                    if (!this.isOutputConnected(i)) {
                        // If not connected, remove any specific widget
                        this.removeLocalWidget(outputName);
                        continue;
                    }

                    // Connected: Find target
                    const linkId = output.links[0]; // Assuming single link for simplicity, or grab first valid
                    const link = app.graph.links[linkId];
                    if (!link) continue;

                    const targetNode = app.graph.getNodeById(link.target_id);
                    if (!targetNode) continue;

                    const targetInput = targetNode.inputs[link.target_slot];
                    if (!targetInput) continue;

                    // Identify target widget (if any)
                    let targetWidget = null;
                    if (targetNode.widgets) {
                        targetWidget = targetNode.widgets.find(w => w.name === targetInput.name);
                    }

                    if (targetWidget) {
                        // Check if we already have a widget for this port
                        let existingWidget = this.findWidgetByName(outputName);

                        // Check if connection changed (different target widget type/name)
                        // simpler to just recreate if anything looks off, but let's try to update if possible
                        if (!existingWidget) {
                            this.createLocalWidget(outputName, targetWidget, targetNode);
                            if (!this.properties.portLabels[i]) {
                                // Default Label: "NodeTitle : InputName"
                                const tTitle = targetNode.title || targetNode.type;
                                let fullLabel = `${tTitle}: ${targetInput.name}`;
                                this.properties.portLabels[i] = fullLabel; // Save full label
                                this.outputs[i].label = this.truncateLabelForWidth(fullLabel); // Display truncated
                            } else {
                                // Update label with truncation if needed
                                this.outputs[i].label = this.truncateLabelForWidth(this.properties.portLabels[i]);
                            }
                        } else {
                            // Update label for existing widget
                            this.outputs[i].label = this.truncateLabelForWidth(this.properties.portLabels[i]);
                        }
                    } else {
                        // Connected to a slot with no widget (e.g. latent, image input)
                        // Remove our widget if it exists
                        this.removeLocalWidget(outputName);
                    }
                }

                this.sortWidgets();
                if (this.computeSize) {
                    try {
                        const minSize = this.computeSize();
                        // Preserve manual size if larger, but grow if needed
                        const currentSize = this.size;
                        this.setSize([
                            Math.max(currentSize[0], minSize[0]),
                            Math.max(currentSize[1], minSize[1])
                        ]);
                    } catch (e) { }
                }
                this.setDirtyCanvas(true, true);
            };

            // --- Sorting & Sizing ---
            nodeType.prototype.sortWidgets = function () {
                if (!this.widgets) return;
                this.widgets.sort((a, b) => {
                    const getIdx = (name) => {
                        const match = name.match(/connect_to_widget_input_(\d+)/);
                        return match ? parseInt(match[1]) : 99999;
                    };
                    return getIdx(a.name) - getIdx(b.name);
                });
            };

            // --- Helpers ---
            nodeType.prototype.truncateLabelForWidth = function (fullLabel) {
                if (!fullLabel) return "";
                const nodeWidth = this.size[0] || 210;
                const margin = 20;
                const portWidth = 20;
                const availableWidth = nodeWidth - margin * 2 - portWidth;
                const charWidth = 10;
                const maxChars = Math.floor(availableWidth / charWidth);

                if (fullLabel.length <= maxChars) return fullLabel;

                // Left truncation: "..." + rightmost characters
                return "..." + fullLabel.slice(-maxChars + 3);
            };

            nodeType.prototype.updateLabelsForWidth = function () {
                // Update all port labels based on current width
                if (!this.outputs || !this.properties.portLabels) return;

                for (let i = 0; i < this.outputs.length; i++) {
                    if (this.properties.portLabels[i]) {
                        const truncatedLabel = this.truncateLabelForWidth(this.properties.portLabels[i]);
                        // Update port label
                        this.outputs[i].label = truncatedLabel;

                        // Update corresponding widget if exists
                        const outputName = this.outputs[i].name;
                        const widget = this.findWidgetByName(outputName);
                        if (widget) {
                            widget.label = truncatedLabel;
                        }
                    }
                }
                this.setDirtyCanvas(true, true);
            };

            nodeType.prototype.findWidgetByName = function (name) {
                if (!this.widgets) return null;
                return this.widgets.find(w => w.name === name);
            }

            nodeType.prototype.removeLocalWidget = function (name) {
                if (!this.widgets) return;
                const idx = this.widgets.findIndex(w => w.name === name);
                if (idx !== -1) {
                    const w = this.widgets[idx];
                    // DOM Cleanup for custom widgets
                    if (w.inputEl) {
                        try { w.inputEl.remove(); } catch (e) { }
                    }
                    if (w.element) {
                        try { w.element.remove(); } catch (e) { }
                    }
                    this.widgets.splice(idx, 1);

                    // Also clear label?
                    const portIdx = this.outputs.findIndex(o => o.name === name);
                    if (portIdx !== -1) {
                        this.outputs[portIdx].label = null;
                        delete this.properties.portLabels[portIdx];
                    }
                }
            };

            nodeType.prototype.createLocalWidget = function (name, targetWidget, targetNode) {
                // Determine type
                let type = "string";
                let options = { ...targetWidget.options };

                const isCombo = targetWidget.type === "combo" || Array.isArray(options.values);
                const isNumber = targetWidget.type === "number" || typeof targetWidget.value === "number";
                const isBoolean = targetWidget.type === "toggle" || typeof targetWidget.value === "boolean";

                let w;
                const callback = (v) => {
                    // Update target
                    targetWidget.value = v;
                    if (targetWidget.callback) {
                        targetWidget.callback(v, app.canvas, targetNode, app.canvas.getPointerPos()); // passing somewhat fake args
                    }
                    targetNode.setDirtyCanvas(true, true);
                };

                if (isCombo) {
                    w = this.addWidget("combo", name, targetWidget.value, callback, options);
                } else if (isBoolean) {
                    w = this.addWidget("toggle", name, targetWidget.value, callback, options);
                } else if (isNumber) {
                    w = this.addWidget("number", name, targetWidget.value, callback, options);
                } else {
                    // String or custom
                    if (targetWidget.type === "customtext" || options?.multiline) {
                        options.multiline = true;
                        // Use ComfyWidgets to create authentic multiline widget
                        const widgetObj = ComfyWidgets["STRING"](this, name, ["STRING", options], app);
                        w = widgetObj.widget;
                        w.value = targetWidget.value;
                        w.callback = callback;
                    } else {
                        w = this.addWidget("string", name, targetWidget.value, callback, options);
                    }
                }

                // Label formatting - direct label assignment with truncation
                const wLabel = targetWidget.label || targetWidget.name;
                // Get the port index to retrieve the full label
                const portIdx = this.outputs.findIndex(o => o.name === name);
                if (portIdx !== -1 && this.properties.portLabels[portIdx]) {
                    // Store full label and set truncated label directly
                    w.fullLabel = this.properties.portLabels[portIdx];
                    w.label = this.truncateLabelForWidth(w.fullLabel);
                } else {
                    w.label = wLabel;
                }
                return w;
            };

            // --- Event Handlers ---
            nodeType.prototype.onConnectionsChange = function (type, index, connected, link_info) {
                if (origOnConnectionsChange) origOnConnectionsChange.apply(this, arguments);
                if (type !== 2) return;
                try {
                    this.managePorts();
                    setTimeout(() => {
                        try { this.refreshWidgets(); } catch (e) { }
                    }, 20);
                } catch (e) {
                }
            };

        }
    }
});
