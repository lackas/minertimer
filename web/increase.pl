#!/opt/invicro/bin/perl
use strict;
use warnings;
use DateTime;
use CGI;

my $cgi = CGI->new;
print "Content-type: text/html\n\n";

my $date = DateTime->now->ymd;
opendir my $dh, 'db';
my %players;
for my $p (qw/david jakob/) {
    $players{ $p } = [ 0, 30, "db/$p-$date", '' ]
}
# = (david => [-1,30,$date,''], jakob => [-1,30,$date,'']);
while (my $f = readdir $dh) {
    next unless $f =~ /(\w+)-$date$/;
    my $u = $1;
    my $fn = "db/$f";
    if ( open my $fh, '<', $fn ) {
        my $time = int <$fh>;
	    my $max = int <$fh>;
        $players{$u} = [ $time, $max, $fn, '' ];
    }
}
my $time = scalar localtime;

for my $u (keys %players) {
    my $fn = $players{$u}[2];
    if (open my $fh, "<", "$fn.increase") {
        my $time = int( <$fh>/60 );
        $players{$u}[3] = ", adding ${time}min";
    }
}

print <<_;
<!DOCTYPE html>
<html>

<head>
    <title>MinerTimer</title>
	<meta name="viewport" content="width=device-width, initial-scale=1">
	<meta http-equiv="refresh" content="10; url=/increase.pl">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/skeleton/2.0.4/skeleton.min.css" />
    <style>
        .button {
            display: inline-block;
            margin: 5px;
            padding: 10px 15px;
            font-size: 16px;
            cursor: pointer;
            text-align: center;
            text-decoration: none;
            outline: none;
            color: #fff;
            background-color: #6C7A89;
            border: none;
            border-radius: 15px;
            box-shadow: 0 9px #999;
        }
        .active { color: green; }
        .inactive { color: gray; }

        .button:hover {background-color: #3E5060}

        .container {
            width: 90%;
            max-width: 600px;
            margin: 0 auto;
            padding-top: 20px;
        }
    </style>
</head>

<body>
<h1>MinerTimer</h1>
<h2>Active Players</h2>
_

if ($cgi->param('user') and $cgi->param('time')) {
	my $u = $cgi->param('user');
    die unless exists $players{$u};
    my $p = $players{$u};
	my $t = int $cgi->param('time');
	print "<h2>Increased time for $u to ", int($t/60), "min</h2>\n";
	open my $fh, '>', "$players{$u}[2].increase";
	print $fh "$t\n";
	print <<_;
<a class="button active" href="/increase.pl">Back</a>
_
} else {
	for my $u (sort keys %players) {
		my $p = $players{$u};
        my $last = int( ( -M $p->[2] ) * 24 * 60 );
        my $style = $last < 5 ? 'active' : 'inactive';
	    print "<h3 class='$style'>$u<h3><h4 class='$style'>", int($p->[0]/60), "/", int($p->[1]/60), "m played (${last}m ago$p->[3])</h4>\n";
		my $off = $p->[1];
	    for my $t (15, 30, 45, 60, 90) {
	        print qq{<a class="button" href="?user=$u&time=}.($t*60+$off).qq{">+$t</a>\n};
	    }
		print "<hr/>";
	}
}

print <<_;
</body>
</html>
_
