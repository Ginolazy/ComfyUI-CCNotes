/** ComfyUI/custom_nodes/CCNotes/js/dynamic_ports.js **/
import { app } from "../../scripts/app.js";

export const DynamicPorts = {
    /**
     * Set up dynamic port functionality for nodes, supporting automatic addition/removal of input or output ports
     * @param {Object} nodeType - Node type
     * @param {Object} options - Configuration options
     * @param {string} options.type - Port type, "input" or "output"
     * @param {string} options.baseName - Base name for the port (e.g. "image" or "connect_to_widget_input")
     * @param {string} options.dataType - Data type for the port (e.g. "IMAGE" or "*")
     * @param {number} options.startIndex - Starting index for ports (default is 1)
     * @param {string} options.secondaryName - Name for secondary port (optional, input mode only)
     * @param {string} options.secondaryDataType - Data type for secondary port (optional, input mode only)
     */
    setupDynamicPorts: function (nodeType, options) {
        const config = {
            startIndex: 1,
            type: "input", // Default to input ports
            ...options
        };

        const isInput = config.type === "input";
        const portArrayKey = isInput ? "inputs" : "outputs";
        const addPortMethod = isInput ? "addInput" : "addOutput";
        const removePortMethod = isInput ? "removeInput" : "removeOutput";
        const isConnectedMethod = isInput ? "isInputConnected" : "isOutputConnected";

        // Save original methods
        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        const origOnConnectionsChange = nodeType.prototype.onConnectionsChange;

        // Add initial ports when node is created
        nodeType.prototype.onNodeCreated = function () {
            if (origOnNodeCreated) {
                origOnNodeCreated.apply(this, arguments);
            }

            // Ensure at least one port exists
            const baseName = `${config.baseName}_${config.startIndex}`;
            if (!this[portArrayKey] || !this[portArrayKey].some(p => p.name === baseName)) {
                this[addPortMethod](baseName, config.dataType);
                // If secondary port exists (input mode only)
                if (isInput && config.secondaryName) {
                    this[addPortMethod](`${config.secondaryName}_${config.startIndex}`, config.secondaryDataType);
                }
            }

            // Initialize storage properties
            this.properties = this.properties || {};
            this.properties[`dynamic${isInput ? 'Inputs' : 'Outputs'}`] = true;

            // Remove excess starting ports (input mode only)
            if (isInput) {
                for (let i = this[portArrayKey].length - 1; i >= 0; i--) {
                    const portName = this[portArrayKey][i].name;
                    if ((portName.startsWith(`${config.baseName}_${config.startIndex + 1}`) ||
                        (config.secondaryName && portName.startsWith(`${config.secondaryName}_${config.startIndex + 1}`)))) {
                        this[removePortMethod](i);
                    }
                }
            }
        };

        // Handle connection changes
        nodeType.prototype.onConnectionsChange = function (type, index, connected, link) {
            if (origOnConnectionsChange) {
                origOnConnectionsChange.apply(this, arguments);
            }

            // Determine if we need to process this connection change
            const shouldProcess = (isInput && type === 1) || (!isInput && type === 2);

            if (shouldProcess) {
                // Use setTimeout for input mode to avoid potential circular issues
                if (isInput) {
                    setTimeout(() => this.updateDynamicPorts(), 10);
                } else {
                    this.updateDynamicPorts();
                }
            }
        };

        // Update dynamic ports
        nodeType.prototype.updateDynamicPorts = function () {

            if (!this.graph) return;

            const groups = new Set();
            const connectedGroups = new Set();

            // Collect current port information
            if (this[portArrayKey]) {
                this[portArrayKey].forEach((port, index) => {
                    if (port.name.startsWith(`${config.baseName}_`)) {
                        const parts = port.name.split("_");
                        const idx = parseInt(parts[parts.length - 1]);
                        if (!isNaN(idx)) {
                            groups.add(idx);
                            if (this[isConnectedMethod](index)) {
                                connectedGroups.add(idx);
                            }
                        }
                    }
                });
            }

            // Find maximum connected index
            const maxIdx = connectedGroups.size > 0 ? Math.max(...connectedGroups) : 0;

            // If all required ports are connected, add new port
            if (connectedGroups.size > 0 && groups.size <= maxIdx) {
                const nextIdx = groups.size + 1;
                const newPortName = `${config.baseName}_${nextIdx}`;
                this[addPortMethod](newPortName, config.dataType);

                // If secondary port exists (input mode only)
                if (isInput && config.secondaryName) {
                    this[addPortMethod](`${config.secondaryName}_${nextIdx}`, config.secondaryDataType);
                }

                if (this.graph) {
                    this.graph._version++;
                    this.setDirtyCanvas(true, true);
                }
            }

            // Clean up unconnected excess ports (keep one unconnected port after max connected index)
            Array.from(groups)
                .filter(idx => !connectedGroups.has(idx) && idx > maxIdx + 1)
                .sort((a, b) => b - a)
                .forEach(idx => {
                    // For input ports, handle both primary and secondary ports
                    if (isInput) {
                        for (let i = this[portArrayKey].length - 1; i >= 0; i--) {
                            const portName = this[portArrayKey][i].name;
                            if ((portName === `${config.baseName}_${idx}`) ||
                                (config.secondaryName && portName === `${config.secondaryName}_${idx}`)) {
                                this[removePortMethod](i);
                            }
                        }
                    } else {
                        // Output ports are simpler, find by name directly
                        for (let i = this[portArrayKey].length - 1; i >= 0; i--) {
                            const portName = this[portArrayKey][i].name;
                            const parts = portName.split("_");
                            const portIdx = parseInt(parts[parts.length - 1]);
                            if (!isNaN(portIdx) && portIdx === idx && portName.startsWith(`${config.baseName}_`)) {
                                this[removePortMethod](i);
                            }
                        }
                    }
                });

            // Update node size and canvas
            if (this.computeSize) {
                this.computeSize();
            }
            if (this.setDirtyCanvas) {
                this.setDirtyCanvas(true, true);
            }
        };
    },

    // Compatibility function, maintain compatibility with existing code
    setupDynamicInputs: function (nodeType, options) {
        this.setupDynamicPorts(nodeType, {
            type: "input",
            baseName: options.baseInputName,
            dataType: options.inputType,
            startIndex: options.startIndex || 1,
            secondaryName: options.secondaryInputName,
            secondaryDataType: options.secondaryInputType
        });
    },

    // Compatibility function, maintain compatibility with existing code
    setupDynamicOutputs: function (nodeType, options) {
        this.setupDynamicPorts(nodeType, {
            type: "output",
            baseName: options.baseOutputName,
            dataType: options.outputType,
            startIndex: options.startIndex || 1
        });
    }
}; 
