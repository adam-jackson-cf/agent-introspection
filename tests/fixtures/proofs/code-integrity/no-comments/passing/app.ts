// @ts-expect-error the fixture checks directives only
const url: number = "https://example.com/a//b";
// oxlint-disable-next-line no-unused-vars
const pattern = /\/\/[^/]*\/\*/g;
const template = `a // b ${url ? "/*" : "*/"} c`;
const quote = 'it\'s // fine';
export { url, pattern, template, quote };
