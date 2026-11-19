/**
 * The id of this build of the page. The panel server writes it into the page
 * when it hands it out and repeats it with every state answer.
 */
let id = "";

export function readBuildId(): void {
  id = document.querySelector('meta[name="ui"]')?.getAttribute("content") ?? "";
}

export const BUILD_ID = (): string => id;
