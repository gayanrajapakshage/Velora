import Link from "next/link";

export function BrandMark({ className }: { className?: string }) {
  return (
    <Link href="/" className={className} aria-label="Velora home">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path
          fill="currentColor"
          d="M5.2 5h13.6A2.2 2.2 0 0 1 21 7.2v9.6A2.2 2.2 0 0 1 18.8 19H5.2A2.2 2.2 0 0 1 3 16.8V7.2A2.2 2.2 0 0 1 5.2 5Zm-.7 5.2a1.5 1.5 0 1 0 0 3.6V16.8c0 .22.18.4.4.4h14.2c.22 0 .4-.18.4-.4V7.2c0-.22-.18-.4-.4-.4H4.9c-.22 0-.4.18-.4.4v3z"
        />
        <path
          fill="currentColor"
          d="M8 9.1h8v1.2H8zm0 2.3h5.2v1.2H8z"
          opacity="0.55"
        />
      </svg>
      Velora
    </Link>
  );
}
