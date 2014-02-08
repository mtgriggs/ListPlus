// ListPlus.cpp : Defines the entry point for the console application.
//

#include "stdafx.h"


int _tmain(int argc, _TCHAR* argv[])
{
   linkedlist list = linkedlist();
   list.insert(1);
   list.print();
   list.insert(2);
   list.print();
   list.insert(3);
   list.print();
   list.remove(2);
   list.print();
   return 0;
}

