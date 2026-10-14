// 32-bit host for the TriBrer/Semight AT4308 attenuator DLL (Inst_ST_OptAtt.dll + LX_VOA.dll).
// The vendor DLLs are 32-bit, so 64-bit Python cannot load them; Python runs this host and
// talks to it one line at a time over stdin/stdout.
//
// Only the calls the station needs are exposed. Calibration offsets and the network
// settings (setIPaddress, set*Offset, ...) are deliberately left out.
//
// Commands (one per line)          Reply
//   open <com number>              OK
//   info                           OK <product>|<serial>|<version>|<channels>
//   wavelengths                    OK <nm> <nm> ...              (wavelength ID = position, from 1)
//   read                           OK att=a1,..;in=p1,..;out=q1,..;wlid=i1,..
//   set_wl <ch> <wavelength id>    OK
//   set_att <ch> <dB>              OK
//   close                          OK
// Any failure replies:              ERR <message>
using System;
using System.Globalization;
using System.Linq;
using LX.TestLibrary;

class VoaHost
{
    const uint Dev = 0;
    static Inst_ST_OptAtt voa;

    static string Join<T>(T[] values, Func<T, string> f) { return string.Join(",", values.Select(f).ToArray()); }

    static string F(float v) { return v.ToString("0.###", CultureInfo.InvariantCulture); }

    static string Handle(string[] p)
    {
        switch (p[0])
        {
            case "open":
                voa = new Inst_ST_OptAtt(Dev, uint.Parse(p[1]));
                return "OK";
            case "info":
                return "OK " + voa.queryProductName(Dev).Trim() + "|" + voa.querySerialNumber(Dev).Trim() + "|"
                    + voa.queryVersion(Dev).Trim() + "|" + voa.queryChannelCount(Dev);
            case "wavelengths":
                return "OK " + string.Join(" ", voa.queryWaveLengthList(Dev).Select(w => w.ToString()).ToArray());
            case "read":
            {
                // Per-channel calls: the all-channel array calls return only 4 values, and
                // queryOutputStatus is unreliable (wrong on CH2, throws on CH5-8), so it is not used.
                int n = voa.queryChannelCount(Dev);
                var att = new float[n]; var pin = new float[n]; var pout = new float[n];
                var wl = new int[n];
                for (uint ch = 1; ch <= n; ch++)
                {
                    att[ch - 1] = voa.queryAttenuation(Dev, ch);
                    pin[ch - 1] = voa.queryInputPower(Dev, ch);
                    pout[ch - 1] = voa.queryOutputPower(Dev, ch);
                    wl[ch - 1] = voa.queryChanWaveLengthID(Dev, ch);
                }
                return "OK att=" + Join(att, F) + ";in=" + Join(pin, F) + ";out=" + Join(pout, F)
                    + ";wlid=" + Join(wl, i => i.ToString());
            }
            case "set_wl":
                voa.setWaveLength(Dev, uint.Parse(p[1]), int.Parse(p[2]));
                return "OK";
            case "set_att":
                voa.setAttenuation(Dev, uint.Parse(p[1]), float.Parse(p[2], CultureInfo.InvariantCulture));
                return "OK";
            case "close":
                if (voa != null) voa.closeDevice(Dev);
                voa = null;
                return "OK";
            default:
                return "ERR unknown command " + p[0];
        }
    }

    static void Main()
    {
        string line;
        while ((line = Console.ReadLine()) != null)
        {
            var parts = line.Trim().Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 0) continue;
            string reply;
            try
            {
                if (voa == null && parts[0] != "open" && parts[0] != "close") reply = "ERR not open";
                else reply = Handle(parts);
            }
            catch (Exception e)
            {
                reply = "ERR " + (e.InnerException ?? e).Message.Replace("\r", " ").Replace("\n", " ");
            }
            Console.WriteLine(reply);
            Console.Out.Flush();
            if (parts[0] == "close") break;
        }
    }
}
