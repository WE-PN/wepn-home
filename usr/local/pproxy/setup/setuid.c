#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <stdlib.h>

//#define DEBUG
#define SRV_CNT 8
#define CMD_CNT 6
#define SPECIAL_CMD_CNT 32

char* _sanitize(char input[], short type) {
	static char ok_chars[] = "abcdefghijklmnopqrstuvwxyz"
		"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
		"1234567890_-.@+=";

	int i = 0;
	while (input[i] != '\0') {
		if (strchr(ok_chars, input[i]) == NULL) {
			//illegal character, remove
			//unless it's a / in a field expected to be base64 (type 1)
			if (!(type == 1 && input[i] == '/')) {
				input[i] = '_';
			}
		}
		i++;
	}
#ifdef DEBUG
	puts(input);
#endif
	return input;
}

char* sanitize(char input[]) {
	return _sanitize(input, 0);
}


char* sanitize_b64(char input[]) {
	return _sanitize(input, 1);
}



int main(int argc, char * argv[])
{
	FILE *p;
	int ch;
	int i = 0;
	const char* services[SRV_CNT];
	services[0]="openvpn";
	services[1]="shadowsocks-libev";
	services[2]="wg-quick@wg%s";
	services[3]="tor";
	services[4]="ssh";
	services[5]="vncserver-x11-serviced";
	services[6]="wepn-unbounded";
	services[7]="wepn-haproxy";


	const char* commands[CMD_CNT];
	commands[0]="stop";
	commands[1]="start";
	commands[2]="restart";
	commands[3]="reload";
	commands[4]="enable";
	commands[5]="disable";


	const char* scommands[SPECIAL_CMD_CNT];
	scommands[0]="/sbin/poweroff";
	scommands[1]="/bin/sh /usr/local/sbin/restart-pproxy.sh";
	scommands[2]="/sbin/reboot";
	scommands[3]="/bin/sh /usr/local/sbin/update-pproxy.sh";
	scommands[4]="/bin/sh /usr/local/sbin/update-system.sh";
	scommands[5]= "/usr/local/sbin/wepn_git.sh";
	scommands[6]= "/usr/bin/wg set wg%s peer %s preshared-key /var/local/pproxy/users/%s/psk allowed-ips %s/32";
	scommands[7]= "/usr/bin/wg-quick save wg%s";
	scommands[8]= "/bin/sh /usr/local/sbin/iptables-flush.sh";
	scommands[9]= "/bin/bash /usr/local/sbin/prevent_location_issue.sh";
	scommands[10]= "/bin/bash /usr/local/sbin/ip-shadow.sh";
	// hard coding sda1 since this command is primarily used in factory for provisioning configs
	scommands[11]= "mount -o umask=0022,gid=1001,uid=1001 /dev/sda1 /mnt/";
	scommands[12]= "ls /dev/sda* | xargs -r -n1 umount -l";
	scommands[13]= "ssh-keygen -A";
	scommands[14]= "chown root.pproxy /var/local/pproxy/ledmanagersocket.sock; chmod 660 /var/local/pproxy/ledmanagersocket.sock";
	scommands[15]= "/usr/bin/systemctl stop wepn-api ; /usr/bin/sleep 1 && /usr/bin/systemctl start wepn-api";
	scommands[16]= "/usr/bin/wg-quick down wg%s";
	scommands[17]= "/usr/bin/wg set wg%s peer %s remove";
	scommands[18]= "echo \"deb [signed-by=/etc/apt/keyrings/repo.we-pn.com.gpg] https://repo.we-pn.com/debian/ testing main\" > /etc/apt/sources.list.d/wepn-beta.list";
	scommands[19]= "rm /etc/apt/sources.list.d/wepn-beta.list";
	// hard coding sda2 for rootfs
	scommands[20]= "mount /dev/sda2 /mnt/";
	scommands[21]= "/usr/bin/wg-quick down wg0";
	scommands[22]= "/usr/bin/wg-quick up wg0";
	scommands[23]= "/usr/local/sbin/limit_bandwidth.sh %s %s %s %s";
	scommands[24]= "/usr/local/sbin/limit_bandwidth.sh get %s %s";
	scommands[25]= "/usr/local/sbin/limit_bandwidth.sh clear %s %s";
	scommands[26]= "/usr/bin/apt-get -y -o Dpkg::Options::=\"--force-confdef\" -o Dpkg::Options::=\"--force-confnew\" -f install wepn-unbounded";
	scommands[27]= "/usr/bin/apt-get -y -o Dpkg::Options::=\"--force-confdef\" -o Dpkg::Options::=\"--force-confnew\" -f install cloudflare-warp";
	scommands[28]= "/usr/bin/apt-get -y -o Dpkg::Options::=\"--force-confdef\" -o Dpkg::Options::=\"--force-confnew\" -f install redsocks";
	scommands[29]= "/usr/bin/wg show wg0 transfer";
	scommands[30]= "/bin/bash /usr/local/sbin/issue-ssl-cert.sh";
	scommands[31]= "/usr/bin/vcgencmd get_throttled";

#define SAFE_SNPRINTF(buf, ...) do { \
	int _written = snprintf(buf, sizeof(buf), __VA_ARGS__); \
	if (_written < 0 || _written >= (int)sizeof(buf)) { \
		printf("Error: command buffer overflow or formatting error!\n"); \
		return(-1); \
	} \
} while(0)

	int c,s,t;

	char cmd[2048] = {0};
	char scmd[1024] = {0};
#ifdef DEBUG
	for (int i = 0; i < argc; i++) {
		printf("param[%d] = %s\n", i, argv[i]);
	}
#endif

	if (argc <3) {
		// help line
		printf(" usage: ./run type service_identifier command_identifier");
		printf("\n* type: \n\t 0: services \n\t 1: special commands");

		printf("\n* services:\n");
		for (i=0; i < SRV_CNT; i++){
			printf("%d: %s \t\t\n", i, services[i]);
		}
		printf("\n* commands:\n");
		for (i=0; i < CMD_CNT; i++){
			printf("%d: %s\t\t\n", i, commands[i]);
		}
		printf("\n* special commands: \n");
		for (i=0; i < SPECIAL_CMD_CNT; i++){
			printf("%d: ", i);
			printf(": ");
			puts(scommands[i]);
			printf("\t\t\n");
		}
		printf("\n");
		return(0);
	}
	char* ptr;
	t = strtol(argv[1], &ptr, 10);
	if (ptr == argv[1] || *ptr != '\0') {
		printf("Invalid type argument: %s\n", argv[1]);
		return(-1);
	}
	s = strtol(argv[2], &ptr, 10);
	if (ptr == argv[2] || *ptr != '\0') {
		printf("Invalid index argument: %s\n", argv[2]);
		return(-1);
	}
#ifdef DEBUG
	printf("t=%d s=%d argc=%d\n", t, s, argc);
#endif


	if (s < 0 || s >= SPECIAL_CMD_CNT || t < 0 || t > 3) {
		printf("S=%d T=%d\n", s, t);
		printf("# commands = %d\n", SPECIAL_CMD_CNT);
		printf("Out of range index\n");
		return(-1);
	}

	if (t == 0) {
		// command is to control services
		if (s >= SRV_CNT) {
			printf("Out of range services index\n");
			return(-1);
		}
		// get the command index:
		c = strtol(argv[3], &ptr, 10);
		if (ptr == argv[3] || *ptr != '\0') {
			printf("Invalid command argument: %s\n", argv[3]);
			return(-1);
		}
		if (c < 0 || c >= CMD_CNT) {
			printf("Out of range commands index\n");
			return(-1);
		}
		if (s == 2) {
			if (argc != 5) {
				printf("Missing params: assuming wg0\n");
				SAFE_SNPRINTF(scmd, "wg-quick@wg0");
			}
			else {
				// update wg%d to match incoming interface
				// wg index:
				sanitize(argv[4]);
				SAFE_SNPRINTF(scmd, services[2], argv[4]);
			}
			SAFE_SNPRINTF(cmd, "systemctl %s %s", commands[c], scmd);
		} else {
			SAFE_SNPRINTF(cmd, "systemctl %s %s", commands[c], services[s]);
		}
	}

	if (t == 1) {
		// special commands
		if (s == 17) {
			// spcial commands that takes in arguments
			if (argc != 5) {
				printf("Missing params: provide wg index and peer string\n");
				return(-1);
			}
			// wg set wg%s peer %s remove

			// wg index:
			sanitize(argv[3]);
			// peer public key
			sanitize_b64(argv[4]);
			SAFE_SNPRINTF(cmd, scommands[s], argv[3], argv[4]);
		}
		else if (s == 6) {
			// spcial commands that takes in arguments
			if (argc != 7) {
				printf("Missing params: provide wg index and peer string\n");
				return(-1);
			}
			// wg set wg%s peer %s allowed-ips preshared-key %s 0.0.0.0/0

			// wg index:
			sanitize(argv[3]);
			// peer public key:
			sanitize_b64(argv[4]);
			// psk
			sanitize(argv[5]);
			// allowed ip
			sanitize(argv[6]);

			SAFE_SNPRINTF(cmd, scommands[s], argv[3], argv[4], argv[5], argv[6]);
		}
		else if (s == 23) {
			// spcial commands that takes in arguments
			if (argc != 7) {
				printf("Missing params: provide <set|remove> <username> <interface> [rate]\n");
				return(-1);
			}

			// set/remove:
			sanitize(argv[3]);
			// username:
			sanitize_b64(argv[4]);
			// interface
			sanitize(argv[5]);
			// rate
			sanitize(argv[6]);

			SAFE_SNPRINTF(cmd, scommands[s], argv[3], argv[4], argv[5], argv[6]);
		}
		else if (s == 24 || s == 25) {
			// 24 just gets the usage from iptables mangle rule
			// 25 resets the counter
			if (argc != 5) {
				printf("Missing params: username device\n");
				return(-1);
			}

			// username
			sanitize(argv[3]);
			// device
			sanitize(argv[4]);

			SAFE_SNPRINTF(cmd, scommands[s], argv[3], argv[4]);
		}
		else if (s == 7 || s == 16) {
			// spcial commands that takes in arguments
			if (argc != 4) {
				printf("Missing params: assuming wg0\n");
				SAFE_SNPRINTF(cmd, scommands[s], "0");
			} else {
				// 7: wg-quick save wg%s
				// or
				// 16: wg-quick down wg%s
				// wg index:
				sanitize(argv[3]);
				SAFE_SNPRINTF(cmd, scommands[s], argv[3]);
			}
		} else {
			SAFE_SNPRINTF(cmd, "%s", scommands[s]);
		}
	}
#ifdef DEBUG
	printf("\ncmd= %s\n", cmd);
#endif
	if (cmd[0] == '\0') {
		printf("No command was built for t=%d s=%d\n", t, s);
		return(1);
	}
	setuid(0);
	p = popen(cmd,"r");
	if ( p == NULL )
	{
		puts("Unable to open process");
		return(1);
	}
	while( (ch=fgetc(p)) != EOF)
		putchar(ch);
	pclose(p);

	return(0);
}
