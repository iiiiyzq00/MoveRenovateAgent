import type { FlowChartStyleOptions } from './diagrams/flowchart/styles.js';
import type { DiagramStylesProvider } from './diagram-api/types.js';
export declare function cssStyleSheetToString(cssStyleSheet: CSSStyleSheet): string;
declare const getStyles: (type: string, userStyles: string, options: {
    fontFamily: string;
    fontSize: string;
    textColor: string;
    errorBkgColor: string;
    errorTextColor: string;
    lineColor: string;
} & FlowChartStyleOptions) => string;
export declare const addStylesForDiagram: (type: string, diagramTheme?: DiagramStylesProvider) => void;
export default getStyles;
