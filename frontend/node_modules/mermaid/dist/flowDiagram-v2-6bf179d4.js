import { p as parser, f as flowDb } from "./flowDb-99855667.js";
import { f as flowRendererV2, a as flowStyles } from "./styles-38506eb2.js";
import { p as setConfig } from "./mermaid-59c9be08.js";
import "d3";
import "dagre-d3-es/src/dagre-js/label/add-html-label.js";
import "dagre-d3-es/src/graphlib/index.js";
import "./index-bf99f535.js";
import "dagre-d3-es/src/dagre/index.js";
import "dagre-d3-es/src/graphlib/json.js";
import "./edges-c9cd0ec5.js";
import "./createText-31840e8c.js";
import "mdast-util-from-markdown";
import "ts-dedent";
import "khroma";
import "dayjs";
import "@braintree/sanitize-url";
import "dompurify";
import "lodash-es/memoize.js";
import "lodash-es/merge.js";
import "stylis";
import "lodash-es/isEmpty.js";
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
