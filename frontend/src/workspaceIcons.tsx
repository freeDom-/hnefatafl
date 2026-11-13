/** Shared line icons for the Arena/Workshop destinations, used by the compact
 * bottom navigation and the desktop sidebar so both keep the same glyphs. */
export function DestinationIcon({ id }: { id: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      {id === 'dashboard' ? (
        <path
          d="M4 10 12 4l8 6v9a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1Z"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
      ) : id === 'play' ? (
        <>
          <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.7" />
          <path d="m10 8.5 6 3.5-6 3.5Z" fill="currentColor" />
        </>
      ) : id === 'statistics' ? (
        <path
          d="M5 19V11m5 8V5m5 14v-6m5 6V8"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
        />
      ) : id === 'leaderboard' ? (
        <path
          d="M8 4h8v5a4 4 0 0 1-8 0Zm0 0H5v2a3 3 0 0 0 3 3m8-5h3v2a3 3 0 0 1-3 3m-4 3v3m-3 0h6"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      ) : id === 'training' ? (
        <path
          d="M4 5h16v12H4Zm0 16h16M8 9h8m-8 4h5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : id === 'tournaments' ? (
        <path
          d="M7 4h10v3a5 5 0 0 1-10 0Zm5 8v4m-4 4h8m-6-4h4M5 5h2M17 5h2"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : id === 'model-collection' ? (
        <path
          d="m12 3 8 4.5v9L12 21l-8-4.5v-9Zm0 0v18m8-13.5-8 4.5-8-4.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      ) : id === 'workshop-workers' ? (
        <path
          d="M12 3.5 4 8v8l8 4.5 8-4.5V8Zm0 0v17m8-12.5-8 4.5-8-4.5m4-2 8 4.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      ) : (
        <path
          d="M4 12a8 8 0 1 0 8-8v8Z"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}
