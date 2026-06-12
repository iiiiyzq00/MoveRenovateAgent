import { p as parser, f as flowDb } from "./flowDb-0cff9c32.js";
import { f as flowRendererV2, g as flowStyles } from "./styles-f765b6ef.js";
import { u as setConfig } from "./mermaid-587d3f71.js";
import "./graph-c30cd682.js";
import "./index-d7be97b0.js";
import "./layout-65045e14.js";
import "./clone-b2142e3d.js";
import "./edges-32cb7e41.js";
import "./createText-53e6bd71.js";
import "./line-b3632e8e.js";
import "./array-b7dcf730.js";
import "./path-39bad7e2.js";
import "./channel-bd41235e.js";
const diagram = {
  parser,
  db: flowDb,
  renderer: flowRendererV2,
  styles: flowStyles,
  init: (cnf) => {
    if (!cnf.flowchart) {
      cnf.flowchart = {};
    }
    cnf.flowchart.arrowMarkerAbsolute = cnf.arrowMarkerAbsolute;
    setConfig({ flowchart: { arrowMarkerAbsolute: cnf.arrowMarkerAbsolute } });
    flowRendererV2.setConf(cnf.flowchart);
    flowDb.clear();
    flowDb.setGen("gen-2");
  }
};
export {
  diagram
};
