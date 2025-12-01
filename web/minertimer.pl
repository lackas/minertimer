#!/opt/invicro/bin/perl
use strict;
use warnings;

print "Content-type: text/plain\n\n";
# print "Hello World: ", scalar localtime, "\n";
my (undef, undef, $user, $date, $play, $max) = split '/', $ENV{REQUEST_URI};

die unless $user =~ /^\w+$/;
die unless $date =~ /^\d{4}-\d{2}-\d{2}$/;
die unless $play =~ /^\d+$/;
die unless $max =~ /^\d+$/;

my $fn = "db/$user-$date";
if (open my $fh, '>', $fn) {
    print $fh "$play\n$max\n";
}

$fn .= '.increase';
if (open my $fh, '<', $fn) {
    $max = int <$fh>;
    unlink $fn;
}

print $max;
