// Hash routing (#/applications/ID) works on any static host without server rules.
export const navigate = (to) => {
  window.location.hash = to;
};