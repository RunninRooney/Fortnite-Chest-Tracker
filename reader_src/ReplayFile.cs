using System.Text;

namespace ChestReplayReader;

public static class ReplayFile
{
    private const uint Magic = 0x1CA2E27F;

    /// <summary>
    /// True while Fortnite is still recording this replay. Other replay tools report that Fortnite only
    /// writes the encryption key once the match ends; <see cref="LiveKeyLength"/> checks that directly.
    /// Reads just the small file header (UE LocalFileNetworkReplayStreaming FLocalFileReplayInfo).
    /// </summary>
    public static bool IsStillRecording(string path) => ReadHeader(path).Live;

    /// <summary>The header's encryption key length (0 = not written yet), and whether the data is encrypted.</summary>
    public static (bool Encrypted, int KeyLength) LiveKeyLength(string path)
    {
        var h = ReadHeader(path);
        return (h.Encrypted, h.KeyLength);
    }

    /// <summary>
    /// Clears bIsLive in an in-memory copy of a replay. Unreal.Core won't read an encrypted replay that's
    /// still marked live, even when its key is already in the header; the data chunks Fortnite has
    /// written so far are complete, and a half-written last chunk ends the read early (kept as partial).
    /// </summary>
    public static void ClearLiveFlag(byte[] replay)
    {
        using var reader = new System.IO.BinaryReader(new MemoryStream(replay), Encoding.Unicode);
        if (replay.Length < 64 || reader.ReadUInt32() != Magic)
        {
            return;
        }

        var fileVersion = reader.ReadUInt32();
        if (fileVersion >= 6)
        {
            var count = reader.ReadInt32();
            reader.BaseStream.Seek(count * 20L, SeekOrigin.Current);
        }

        reader.BaseStream.Seek(12, SeekOrigin.Current);  // LengthInMS, NetworkVersion, Changelist
        var nameLength = reader.ReadInt32();
        reader.BaseStream.Seek(nameLength < 0 ? -nameLength * 2L : nameLength, SeekOrigin.Current);
        var at = (int)reader.BaseStream.Position;
        if (at + 4 <= replay.Length)
        {
            Array.Clear(replay, at, 4);  // bIsLive = 0
        }
    }

    private static (bool Live, bool Encrypted, int KeyLength) ReadHeader(string path)
    {
        try
        {
            using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            var buffer = new byte[(int)Math.Min(fs.Length, 8192)];
            fs.ReadExactly(buffer);

            using var reader = new System.IO.BinaryReader(new MemoryStream(buffer), Encoding.Unicode);
            if (reader.ReadUInt32() != Magic)
            {
                return (false, false, 0);
            }

            var fileVersion = reader.ReadUInt32();
            if (fileVersion >= 6) // HISTORY_CUSTOM_VERSIONS: count, then (FGuid, int32) pairs
            {
                var count = reader.ReadInt32();
                reader.BaseStream.Seek(count * 20L, SeekOrigin.Current);
            }

            reader.ReadUInt32(); // LengthInMS
            reader.ReadUInt32(); // NetworkVersion
            reader.ReadUInt32(); // Changelist

            var nameLength = reader.ReadInt32(); // FString: negative length = UTF-16
            reader.BaseStream.Seek(nameLength < 0 ? -nameLength * 2L : nameLength, SeekOrigin.Current);

            var live = reader.ReadUInt32() != 0; // bIsLive
            bool encrypted = false;
            var keyLength = 0;
            if (fileVersion >= 3) reader.ReadInt64();  // Timestamp
            if (fileVersion >= 2) reader.ReadUInt32(); // bCompressed
            if (fileVersion >= 6)
            {
                encrypted = reader.ReadUInt32() != 0;  // bEncrypted
                keyLength = reader.ReadInt32();        // EncryptionKey: TArray<uint8> count
                // While the match is on, Fortnite writes a placeholder key of zeros; the real one only at
                // the end (seen Nov 19, 2026). A zero key can't decrypt anything: report it as no key.
                if (keyLength > 0 && keyLength <= 64)
                {
                    var key = reader.ReadBytes(keyLength);
                    if (key.Length == keyLength && key.All(b => b == 0)) keyLength = 0;
                }
            }

            return (live, encrypted, keyLength);
        }
        catch (Exception ex) when (ex is IOException or EndOfStreamException or ArgumentException)
        {
            return (false, false, 0);
        }
    }
}
